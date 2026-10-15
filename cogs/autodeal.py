"""
Auto MM deal flow (modeled on the recorded Jace's Auto Middleman flow).

 form modal -> ticket -> pick roles -> confirm info -> set USD amount -> confirm
 -> payment info -> (payment detected) -> FUNDED
 FUNDED:  sender "Release"  -> receiver enters address -> confirm -> funds go to receiver
          anyone "Cancel"   -> BOTH must confirm -> sender enters address -> confirm -> refund to sender
          if either side declines the cancel, the deal goes back to FUNDED.

All buttons are persistent (fixed custom_ids). The deal is looked up by ticket channel id,
so everything survives a bot restart.
"""
import asyncio
import random
import re
import time
from datetime import datetime

import discord
from discord.ext import commands, tasks

from cogs.panel import CloseTicketView, close_ticket_channel, load_config
from utils import deals as store
from utils.embeds import get_color
from utils.wallet import (
    COINS, WalletError, WalletPendingError, calc_fee, fmt_amount, get_price_usd, get_wallet, validate_address,
)
from cogs.stats import record_completed_deal, sync_rank_roles

PAYMENT_WINDOW = 45 * 60  # allow up to 45 minutes for an LTC payment to confirm
DASH = "\u2014"

_locks = {}
_completion_inflight = set()


def lock_for(channel_id):
    return _locks.setdefault(channel_id, asyncio.Lock())


def E(config, title, desc=None, kind="main"):
    e = discord.Embed(title=title, description=desc, color=get_color(config, kind))
    e.set_footer(text=config.get("footer", "Time AutoMM"))
    return e


def mention(uid):
    return f"<@{uid}>" if uid else DASH


def coin_of(deal):
    return COINS[deal["coin"]]["label"]


async def get_deal(interaction, stages=None):
    """Fetch the deal for this ticket, make sure the clicker is a party and the stage fits."""
    deal = store.get(interaction.channel_id)
    if not deal:
        await interaction.response.send_message("❌ No active deal in this channel.", ephemeral=True)
        return None
    if interaction.user.id not in (deal["creator_id"], deal["trader_id"]):
        await interaction.response.send_message("❌ You are not part of this deal.", ephemeral=True)
        return None
    if stages and deal["stage"] not in stages:
        await interaction.response.send_message("⚠️ That button is no longer active.", ephemeral=True)
        return None
    return deal


def other_party(deal, uid):
    return deal["trader_id"] if uid == deal["creator_id"] else deal["creator_id"]


async def resolve_member(guild, raw):
    raw = (raw or "").strip().lstrip("@")
    m = re.fullmatch(r"<@!?(\d+)>", raw)
    uid = int(m.group(1)) if m else (int(raw) if raw.isdigit() else None)
    if uid:
        member = guild.get_member(uid)
        if member:
            return member
        try:
            return await guild.fetch_member(uid)
        except discord.NotFound:
            return None
    low = raw.lower()
    return discord.utils.find(
        lambda mem: mem.name.lower() == low or mem.display_name.lower() == low, guild.members
    )


# ==================== STEP 1: FORM + TICKET ====================

class DealFormModal(discord.ui.Modal):
    def __init__(self, coin):
        super().__init__(title="Fill out the format")
        self.coin = coin
        self.trader = discord.ui.TextInput(
            label="Paste Your Trader's Username or ID",
            placeholder="e.g.: kookie.js / 1331012274151751743", max_length=40)
        self.giving = discord.ui.TextInput(
            label="What are You giving?", style=discord.TextStyle.paragraph, max_length=300)
        self.trader_giving = discord.ui.TextInput(
            label="What is Your Trader giving?", style=discord.TextStyle.paragraph, max_length=300)
        for item in (self.trader, self.giving, self.trader_giving):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        config = load_config()
        guild = interaction.guild
        if config.get("wallet", {}).get("kill_switch"):
            return await interaction.response.send_message(
                "⛔ Auto MM is paused right now. Please use a human middleman or try later.", ephemeral=True)
        trader = await resolve_member(guild, self.trader.value)
        if not trader or trader.bot:
            return await interaction.response.send_message(
                "❌ I couldn't find that user in this server. Use their exact username or ID.", ephemeral=True)
        if trader.id == interaction.user.id:
            return await interaction.response.send_message("❌ You can't trade with yourself.", ephemeral=True)
        black = guild.get_role(config["roles"].get("blacklisted", 0))
        if black and black in trader.roles:
            return await interaction.response.send_message("❌ That user is blacklisted.", ephemeral=True)
        category = guild.get_channel(config["categories"].get("auto_tickets", 0))
        if not category:
            return await interaction.response.send_message(
                "❌ `auto_tickets` category ID is missing or wrong in config.json", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        perms = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: perms,
            trader: perms,
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True, manage_messages=True),
        }
        for rname in ("staff", "admin"):
            r = guild.get_role(config["roles"].get(rname, 0))
            if r:
                overwrites[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)

        deal_id = f"{random.randint(100000, 999999)}"
        name = f"{self.coin.lower()}-{interaction.user.name[:10]}-{deal_id[-4:]}"
        ticket = await category.create_text_channel(
            name=name, overwrites=overwrites,
            topic=f"AutoMM|{self.coin}|{interaction.user.id}|{trader.id}|{deal_id}")

        deal = {
            "id": deal_id, "channel_id": ticket.id, "coin": self.coin,
            "creator_id": interaction.user.id, "trader_id": trader.id,
            "creator_giving": self.giving.value, "trader_giving": self.trader_giving.value,
            "sender_id": None, "receiver_id": None,
            "stage": "roles", "confirms": [],
            "usd": None, "fee": 0.0, "coin_amount": None, "payout_amount": None,
            "address": None, "received": None, "deposit_txid": None, "expires": None,
            "pending_address": None, "payout_to": None, "result": None,
            "created": time.time(),
        }
        store.save(deal)

        info = E(config, "👋 • Time AutoMM Service",
                 "Make sure to follow the steps and read the instructions thoroughly.\n"
                 "Please explicitly state the trade details if the information below is inaccurate.\n"
                 f"By using this bot, you agree to our ToS: {config.get('tos_channel', '#tos-crypto')}\n"
                 "Whether you're the seller or the buyer, make sure to video-record everything in this deal.",
                 "accent")
        info.add_field(name=f"{interaction.user.display_name}'s side:", value=f"```{self.giving.value}```", inline=False)
        info.add_field(name=f"{trader.display_name}'s side:", value=f"```{self.trader_giving.value}```", inline=False)
        await ticket.send(content=f"{interaction.user.mention} {trader.mention}", embed=info, view=CloseTicketView())
        await ticket.send(embed=role_embed(config, deal), view=RoleView())

        for key in ("ticket_logs", "deal_logs"):
            ch = guild.get_channel(config["channels"].get(key, 0))
            if ch:
                emb = E(config, f"New Auto MM Ticket ({self.coin})", kind="accent")
                emb.add_field(name="Users", value=f"{interaction.user.mention} ↔ {trader.mention}")
                emb.add_field(name="Ticket", value=ticket.mention)
                await ch.send(embed=emb)

        await interaction.followup.send(f"✅ Ticket created: {ticket.mention}", ephemeral=True)


# ==================== STEP 2: ROLES ====================

def role_embed(config, deal):
    c = coin_of(deal)
    e = E(config, "🛡️ • Select your role",
          f"• **\"Sender\"** if you are Sending {c} to the bot.\n"
          f"• **\"Receiver\"** if you are Receiving {c} later from the bot.")
    e.add_field(name="Sender", value=mention(deal["sender_id"]), inline=True)
    e.add_field(name="Receiver", value=mention(deal["receiver_id"]), inline=True)
    e.add_field(name="\u200b", value=f"⚠️ Selecting the wrong role may result in losing the {c}!", inline=False)
    return e


def roles_check_embed(config, deal):
    e = E(config, "❔ • Is This Information Correct?",
          "Make sure you have selected the right role! If you didn't then click Incorrect.\n"
          "⚠️ Selecting the wrong role may result in losing the LTC/crypto!")
    e.add_field(name="Sender", value=mention(deal["sender_id"]), inline=True)
    e.add_field(name="Receiver", value=mention(deal["receiver_id"]), inline=True)
    return e


class RoleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _pick(self, interaction, role):
        deal = await get_deal(interaction, {"roles", "roles_confirm"})
        if not deal:
            return
        config = load_config()
        me, other = interaction.user.id, other_party(deal, interaction.user.id)
        if role == "sender":
            deal["sender_id"], deal["receiver_id"] = me, other
        else:
            deal["sender_id"], deal["receiver_id"] = other, me
        deal["stage"], deal["confirms"] = "roles_confirm", []
        store.save(deal)
        await interaction.response.edit_message(embed=role_embed(config, deal), view=self)
        await interaction.channel.send(
            content=f"{mention(deal['sender_id'])} {mention(deal['receiver_id'])}",
            embed=roles_check_embed(config, deal), view=RolesConfirmView())

    @discord.ui.button(label="Sender", style=discord.ButtonStyle.primary, custom_id="deal_role_sender")
    async def sender(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "sender")

    @discord.ui.button(label="Receiver", style=discord.ButtonStyle.primary, custom_id="deal_role_receiver")
    async def receiver(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "receiver")

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.secondary, custom_id="deal_role_reset")
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        deal = await get_deal(interaction, {"roles", "roles_confirm"})
        if not deal:
            return
        deal["sender_id"] = deal["receiver_id"] = None
        deal["stage"], deal["confirms"] = "roles", []
        store.save(deal)
        await interaction.response.edit_message(embed=role_embed(load_config(), deal), view=self)


class RolesConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Correct", style=discord.ButtonStyle.success, custom_id="deal_roles_ok")
    async def ok(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with lock_for(interaction.channel_id):
            deal = await get_deal(interaction, {"roles_confirm"})
            if not deal:
                return
            if interaction.user.id in deal["confirms"]:
                return await interaction.response.send_message("You already confirmed.", ephemeral=True)
            deal["confirms"].append(interaction.user.id)
            await interaction.response.send_message(f"✅ {interaction.user.mention} clicked Correct.")
            if len(deal["confirms"]) == 2:
                deal["stage"], deal["confirms"] = "amount", []
                store.save(deal)
                await interaction.message.edit(view=None)
                await interaction.channel.send(
                    content=mention(deal["sender_id"]),
                    embed=E(load_config(), "💵 • Set the amount in USD value"), view=SetAmountView())
            else:
                store.save(deal)

    @discord.ui.button(label="Incorrect", style=discord.ButtonStyle.danger, custom_id="deal_roles_bad")
    async def bad(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with lock_for(interaction.channel_id):
            deal = await get_deal(interaction, {"roles_confirm"})
            if not deal:
                return
            deal["sender_id"] = deal["receiver_id"] = None
            deal["stage"], deal["confirms"] = "roles", []
            store.save(deal)
            await interaction.response.send_message(
                f"❌ {interaction.user.mention} clicked Incorrect. Please select your roles again above.")
            await interaction.message.edit(view=None)


# ==================== STEP 3: AMOUNT ====================

class AmountModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Set USD Amount")
        self.amount = discord.ui.TextInput(label="Please state the amount in USD value", placeholder="0", max_length=12)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            usd = round(float(self.amount.value.replace("$", "").replace(",", "").strip()), 2)
            cap = float(load_config().get("wallet", {}).get("max_usd") or 100000)
            if usd < 0.10 or usd > cap:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "❌ Enter a valid USD amount (minimum $0.10, maximum "
                f"${float(load_config().get('wallet', {}).get('max_usd') or 100000):,.0f}).", ephemeral=True)
        async with lock_for(interaction.channel_id):
            deal = await get_deal(interaction, {"amount"})
            if not deal:
                return
            deal["usd"], deal["stage"], deal["confirms"] = usd, "amount_confirm", []
            store.save(deal)
            e = E(load_config(), f"❔ • USD amount set to ${usd:,.2f}", "Please confirm the USD amount.")
            await interaction.response.send_message(
                content=f"{mention(deal['sender_id'])} {mention(deal['receiver_id'])}",
                embed=e, view=AmountConfirmView())


class SetAmountView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Set USD Amount", style=discord.ButtonStyle.primary, custom_id="deal_set_amount")
    async def set_amount(self, interaction: discord.Interaction, button: discord.ui.Button):
        deal = await get_deal(interaction, {"amount"})
        if deal:
            await interaction.response.send_modal(AmountModal())


class AmountConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Correct", style=discord.ButtonStyle.success, custom_id="deal_amt_ok")
    async def ok(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with lock_for(interaction.channel_id):
            deal = await get_deal(interaction, {"amount_confirm"})
            if not deal:
                return
            if interaction.user.id in deal["confirms"]:
                return await interaction.response.send_message("You already confirmed.", ephemeral=True)
            deal["confirms"].append(interaction.user.id)
            store.save(deal)
            await interaction.response.send_message(f"✅ {interaction.user.mention} confirmed the USD amount.")
            if len(deal["confirms"]) == 2:
                await interaction.message.edit(view=None)
                await send_payment_info(interaction.channel, deal)

    @discord.ui.button(label="Incorrect", style=discord.ButtonStyle.danger, custom_id="deal_amt_bad")
    async def bad(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with lock_for(interaction.channel_id):
            deal = await get_deal(interaction, {"amount_confirm"})
            if not deal:
                return
            deal["stage"], deal["confirms"], deal["usd"] = "amount", [], None
            store.save(deal)
            await interaction.response.send_message(f"❌ {interaction.user.mention} clicked Incorrect.")
            await interaction.message.edit(view=None)
            await interaction.channel.send(
                content=mention(deal["sender_id"]),
                embed=E(load_config(), "💵 • Set the amount in USD value"), view=SetAmountView())


# ==================== STEP 4: PAYMENT ====================

async def send_payment_info(channel, deal):
    config = load_config()
    wallet = get_wallet(config)
    try:
        price = await get_price_usd(deal["coin"])
        address, wallet_index = await wallet.new_deposit_address(deal)
    except Exception as e:
        deal["stage"], deal["confirms"] = "amount", []
        store.save(deal)
        await channel.send(f"❌ {e}\nPlease set the amount again in a moment.",
                           view=SetAmountView())
        return

    fee = calc_fee(config, deal["usd"])
    deal["fee"] = fee
    deal["payout_amount"] = round(deal["usd"] / price, COINS[deal["coin"]]["decimals"])
    deal["coin_amount"] = round((deal["usd"] + fee) / price, COINS[deal["coin"]]["decimals"])
    deal["address"] = address
    deal["wallet_index"] = wallet_index
    deal["expires"] = time.time() + PAYMENT_WINDOW
    deal["stage"] = "awaiting_payment"
    store.save(deal)

    c = coin_of(deal)
    e = E(config, "💳 • Payment Information",
          f"Make sure to send the **EXACT** amount in {c}.\n"
          f"{mention(deal['sender_id'])} is the one who must send the payment.")
    e.add_field(name="USD Amount", value=f"${deal['usd']:,.2f}" + (f" + ${fee:.2f} fee" if fee else ""), inline=True)
    e.add_field(name=f"{c} Amount", value=f"`{fmt_amount(deal['coin'], deal['coin_amount'])}`", inline=True)
    e.add_field(name="Payment Address", value=f"`{address}`", inline=False)
    foot = f"Current {c} price: ${price:,.2f}. This ticket will close if payment is not detected within 45 minutes."
    if getattr(wallet, "mode", "") == "dryrun":
        foot = "🧪 DRY RUN MODE (no real funds). " + foot
    e.set_footer(text=foot)
    await channel.send(content=mention(deal["sender_id"]), embed=e, view=CopyDetailsView())


class CopyDetailsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Copy Details", style=discord.ButtonStyle.primary, custom_id="deal_copy")
    async def copy(self, interaction: discord.Interaction, button: discord.ui.Button):
        deal = await get_deal(interaction)
        if not deal or not deal.get("address"):
            return
        await interaction.response.send_message(
            f"**Address**\n```{deal['address']}```\n**Amount**\n```{fmt_amount(deal['coin'], deal['coin_amount'])}```",
            ephemeral=True)


# ==================== STEP 5: FUNDED -> RELEASE / CANCEL ====================

def funded_embed(config, deal):
    c = coin_of(deal)
    e = E(config, "✅ • Payment Received",
          f"The bot received **{fmt_amount(deal['coin'], deal['received'])} {c}**.\n\n"
          f"• {mention(deal['receiver_id'])}: hand over what you owe (see the trade details above) to the sender.\n"
          f"• {mention(deal['sender_id'])}: once you have received everything, press **Release**.\n"
          "• Press **Cancel Deal** to back out. Both of you must confirm a cancel, and the funds go back to the sender.",
          "success")
    e.add_field(name="Deposit Transaction", value=f"`{(deal.get('deposit_txid') or DASH)[:16]}…`", inline=False)
    return e


class FundedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Release to Receiver", style=discord.ButtonStyle.success, custom_id="deal_release")
    async def release(self, interaction: discord.Interaction, button: discord.ui.Button):
        deal = await get_deal(interaction, {"funded"})
        if not deal:
            return
        if interaction.user.id != deal["sender_id"]:
            return await interaction.response.send_message("❌ Only the sender can release the funds.", ephemeral=True)
        await interaction.response.send_message(
            f"⚠️ This sends the {coin_of(deal)} to the receiver and **cannot be undone**. Are you sure?",
            view=ReleaseSureView(), ephemeral=True)

    @discord.ui.button(label="Cancel Deal", style=discord.ButtonStyle.danger, custom_id="deal_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with lock_for(interaction.channel_id):
            deal = await get_deal(interaction, {"funded"})
            if not deal:
                return
            deal["stage"], deal["confirms"] = "cancel_confirm", []
            store.save(deal)
            await interaction.message.edit(view=None)
            e = E(load_config(), "⚠️ • Cancel Deal?",
                  f"{interaction.user.mention} wants to cancel this deal.\n"
                  f"**Both** {mention(deal['sender_id'])} and {mention(deal['receiver_id'])} must confirm. "
                  f"If confirmed, the {coin_of(deal)} is refunded to the sender.\n"
                  "If either of you declines, the deal continues as normal.", "warning")
            await interaction.response.send_message(
                content=f"{mention(deal['sender_id'])} {mention(deal['receiver_id'])}",
                embed=e, view=CancelConfirmView())


class ReleaseSureView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Yes, release", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with lock_for(interaction.channel_id):
            deal = store.get(interaction.channel_id)
            if not deal or deal["stage"] != "funded" or interaction.user.id != deal["sender_id"]:
                return await interaction.response.edit_message(content="⚠️ No longer valid.", view=None)
            deal["stage"], deal["payout_to"] = "address", "receiver"
            store.save(deal)
        await interaction.response.edit_message(content="✅ Released.", view=None)
        await interaction.channel.send(f"✅ {interaction.user.mention} released the funds.")
        await ask_address(interaction.channel, deal)


class CancelConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Confirm Cancel", style=discord.ButtonStyle.danger, custom_id="deal_cancel_ok")
    async def ok(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with lock_for(interaction.channel_id):
            deal = await get_deal(interaction, {"cancel_confirm"})
            if not deal:
                return
            if interaction.user.id in deal["confirms"]:
                return await interaction.response.send_message("You already confirmed.", ephemeral=True)
            deal["confirms"].append(interaction.user.id)
            await interaction.response.send_message(f"✅ {interaction.user.mention} confirmed the cancellation.")
            if len(deal["confirms"]) == 2:
                deal["stage"], deal["payout_to"] = "address", "sender"
                store.save(deal)
                await interaction.message.edit(view=None)
                await interaction.channel.send("🔁 Both sides confirmed. The funds will be **refunded to the sender**.")
                await ask_address(interaction.channel, deal)
            else:
                store.save(deal)

    @discord.ui.button(label="Keep Deal", style=discord.ButtonStyle.secondary, custom_id="deal_cancel_no")
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with lock_for(interaction.channel_id):
            deal = await get_deal(interaction, {"cancel_confirm"})
            if not deal:
                return
            deal["stage"], deal["confirms"] = "funded", []
            store.save(deal)
            await interaction.response.send_message(f"↩️ {interaction.user.mention} declined the cancellation. The deal continues.")
            await interaction.message.edit(view=None)
            await interaction.channel.send(embed=funded_embed(load_config(), deal), view=FundedView())


# ==================== STEP 6: ADDRESS -> CONFIRM -> PAYOUT ====================

def payee_id(deal):
    return deal["receiver_id"] if deal["payout_to"] == "receiver" else deal["sender_id"]


def transaction_url(coin, txid):
    if coin == "LTC":
        return f"https://live.blockcypher.com/ltc/tx/{txid}/"
    if coin == "SOL":
        return f"https://explorer.solana.com/tx/{txid}"
    return f"https://bscscan.com/tx/{txid}"


class TransactionView(discord.ui.View):
    def __init__(self, url):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="View Transaction", style=discord.ButtonStyle.link, url=url))


async def post_completed_crypto(guild, config, deal, amount, txid):
    """Publish after both parties make a visibility choice; privacy never affects stats."""
    if deal.get("payout_to") == "sender" or deal.get("privacy_finalized"):
        return
    choices = deal.get("publicity", {})
    sender_id, receiver_id = str(deal["sender_id"]), str(deal["receiver_id"])
    if sender_id not in choices or receiver_id not in choices:
        return
    key = str(deal["id"])
    if key in _completion_inflight:
        return
    channel = guild.get_channel(config["channels"].get("completed_crypto", 0))
    if not channel:
        print(f"[completed-crypto] channel is missing for deal {deal['id']}")
        return

    _completion_inflight.add(key)
    try:
        coin = coin_of(deal)
        tx_url = transaction_url(deal["coin"], txid)
        planned_amount = float(deal.get("payout_amount") or 0)
        receiver_usd = (deal["usd"] * amount / planned_amount) if planned_amount else deal["usd"]
        pub = E(config, f"✅ • Trade Complete ({coin})", None, "success")
        pub.add_field(name="Deal amount", value=f"${deal['usd']:,.2f} (`{fmt_amount(deal['coin'], deal['coin_amount'])} {coin}`)", inline=False)
        pub.add_field(name="Receiver received", value=f"${receiver_usd:,.2f} (`{fmt_amount(deal['coin'], amount)} {coin}`)", inline=False)
        sender_display = mention(deal["sender_id"]) if choices[sender_id] else "Anonymous"
        receiver_display = mention(deal["receiver_id"]) if choices[receiver_id] else "Anonymous"
        pub.add_field(name="Participants", value=f"Sender: {sender_display}\nReceiver: {receiver_display}", inline=False)
        pub.add_field(name="Deal", value=f"Completed securely • `{deal['id']}`", inline=False)
        pub.add_field(name="Transaction", value=f"[View transaction]({tx_url})", inline=False)
        existing = await find_completed_message(channel, deal)
        if existing:
            await existing.edit(embed=pub, view=TransactionView(tx_url))
            deal["completed_crypto_message_id"] = existing.id
        else:
            posted = await channel.send(embed=pub, view=TransactionView(tx_url))
            deal["completed_crypto_message_id"] = posted.id
        deal["completed_crypto_posted"] = True
        deal["privacy_finalized"] = True
        store.save(deal)
        ticket = guild.get_channel(deal["channel_id"])
        if ticket and deal.get("privacy_message_in_ticket", True) and deal.get("privacy_message_id"):
            try:
                prompt = await ticket.fetch_message(deal["privacy_message_id"])
                await prompt.edit(view=CloseTicketView(tx_url))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
    except Exception as exc:
        print(f"[completed-crypto] deal {deal['id']} post failed: {exc}")
    finally:
        _completion_inflight.discard(key)


async def find_completed_message(channel, deal):
    message_id = deal.get("completed_crypto_message_id")
    if message_id:
        try:
            return await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    marker = str(deal.get("id"))
    try:
        async for message in channel.history(limit=300):
            if message.author.bot and any(
                field.name == "Deal" and marker in field.value
                for embed in message.embeds for field in embed.fields
            ):
                deal["completed_crypto_message_id"] = message.id
                store.save(deal)
                return message
    except (discord.Forbidden, discord.HTTPException):
        pass
    return None


async def redact_legacy_completed_message(guild, config, deal):
    channel = guild.get_channel(config["channels"].get("completed_crypto", 0))
    if not channel:
        return
    message = await find_completed_message(channel, deal)
    if not message or not message.embeds:
        return
    embed = discord.Embed.from_dict(message.embeds[0].to_dict())
    for index, field in enumerate(embed.fields):
        if field.name == "Participants":
            embed.set_field_at(index, name=field.name,
                               value="Sender: Anonymous\nReceiver: Anonymous", inline=field.inline)
            await message.edit(embed=embed)
            return


class DealPrivacyView(discord.ui.View):
    def __init__(self, channel_id, tx_url=None, include_close=True):
        super().__init__(timeout=None)
        self.channel_id = int(channel_id)
        display = discord.ui.Button(label="Display my name", style=discord.ButtonStyle.success,
                                    custom_id=f"deal_privacy_display:{self.channel_id}")
        private = discord.ui.Button(label="Keep me private", style=discord.ButtonStyle.secondary,
                                    custom_id=f"deal_privacy_private:{self.channel_id}")
        display.callback = self.display_name
        private.callback = self.keep_private
        self.add_item(display)
        self.add_item(private)
        if include_close:
            close = discord.ui.Button(label="Close Ticket", style=discord.ButtonStyle.danger,
                                      custom_id=f"deal_privacy_close:{self.channel_id}")
            close.callback = self.close_ticket
            self.add_item(close)
        if tx_url:
            self.add_item(discord.ui.Button(label="View Transaction", style=discord.ButtonStyle.link, url=tx_url))

    async def choose(self, interaction, visible):
        deal = store.get(self.channel_id)
        if not deal or deal.get("stage") not in {"done", "payout_pending"} or deal.get("privacy_finalized"):
            return await interaction.response.send_message("This deal's visibility choice is closed.", ephemeral=True)
        uid = str(interaction.user.id)
        if uid not in {str(deal.get("sender_id")), str(deal.get("receiver_id"))}:
            return await interaction.response.send_message("Only the deal participants can choose visibility.", ephemeral=True)
        deal.setdefault("publicity", {})[uid] = visible
        store.save(deal)
        await interaction.response.send_message(
            "Your choice is saved. The completed-deal post will appear after both participants choose.",
            ephemeral=True)
        if all(str(person) in deal["publicity"] for person in (deal["sender_id"], deal["receiver_id"])):
            result = deal.get("result") or {}
            await post_completed_crypto(interaction.guild, load_config(), deal,
                                        result.get("sent", deal.get("pending_amount", 0)),
                                        result.get("txid", deal.get("pending_txid")))

    async def display_name(self, interaction):
        await self.choose(interaction, True)

    async def keep_private(self, interaction):
        await self.choose(interaction, False)

    async def close_ticket(self, interaction):
        deal = store.get(self.channel_id)
        if not deal or interaction.user.id not in (deal.get("sender_id"), deal.get("receiver_id")):
            return await interaction.response.send_message("Only the deal participants can close this ticket.", ephemeral=True)
        await interaction.response.defer()
        if deal.get("stage") in {"done", "payout_pending"} and not deal.get("privacy_finalized"):
            choices = deal.setdefault("publicity", {})
            choices.setdefault(str(deal["sender_id"]), False)
            choices.setdefault(str(deal["receiver_id"]), False)
            store.save(deal)
            result = deal.get("result") or {}
            await post_completed_crypto(interaction.guild, load_config(), deal,
                                        result.get("sent", deal.get("pending_amount", 0)),
                                        result.get("txid", deal.get("pending_txid")))
        await close_ticket_channel(interaction.channel, f"{interaction.user} ({interaction.user.id})", interaction)


async def prompt_deal_visibility(channel, deal, tx_url, message=None):
    if deal.get("completed_crypto_posted") and not deal.get("privacy_finalized"):
        await redact_legacy_completed_message(channel.guild, load_config(), deal)
    if deal.get("payout_to") != "sender" and not deal.get("stats_recorded"):
        totals = record_completed_deal(deal)
        await sync_rank_roles(channel.guild, totals)
        deal["stats_recorded"] = True
        store.save(deal)
    config = load_config()
    e = E(config, "✅ • Trade Complete", "Choose whether your own name appears in the completed-deals post. "
          "Each participant chooses separately. If you don't choose before the ticket closes, your name stays anonymous.", "success")
    coin = coin_of(deal)
    result = deal.get("result") or {}
    amount = float(result.get("sent") or deal.get("pending_amount") or 0)
    e.add_field(name="Deal amount", value=f"${deal['usd']:,.2f} (`{fmt_amount(deal['coin'], deal['coin_amount'])} {coin}`)", inline=False)
    e.add_field(name="Receiver received", value=f"${deal['usd']:,.2f} (`{fmt_amount(deal['coin'], amount)} {coin}`)", inline=False)
    e.add_field(name="Participants", value=f"Sender: {mention(deal['sender_id'])}\nReceiver: {mention(deal['receiver_id'])}", inline=False)
    e.add_field(name="Visibility", value="Choose **Display my name** or **Keep me private** below.", inline=False)
    view = DealPrivacyView(deal["channel_id"], tx_url)
    if message:
        saved = await message.edit(content=None, embed=e, view=view)
    else:
        saved = await channel.send(embed=e, view=view)
    deal["privacy_message_id"] = saved.id
    deal["privacy_message_in_ticket"] = True
    deal.setdefault("publicity", {})
    store.save(deal)


async def attach_legacy_privacy_choices(guild, config, deal):
    """Offer choices on the old public card when its ticket is already gone."""
    channel = guild.get_channel(config["channels"].get("completed_crypto", 0))
    if not channel:
        return False
    await redact_legacy_completed_message(guild, config, deal)
    message = await find_completed_message(channel, deal)
    if message and message.embeds:
        embed = discord.Embed.from_dict(message.embeds[0].to_dict())
    else:
        # The old completion card may use a different deal identifier. Keep
        # recovery actionable by posting an anonymous choice card instead of
        # silently defaulting both participants to private.
        result = deal.get("result") or {}
        txid = result.get("txid", deal.get("pending_txid"))
        if not txid:
            return False
        coin = coin_of(deal)
        amount = float(result.get("sent") or deal.get("pending_amount") or 0)
        embed = E(config, f"✅ • Trade Complete ({coin})",
                  "Choose whether your own name appears in the completed-deals post. "
                  "Each participant chooses separately.", "success")
        embed.add_field(name="Deal amount", value=f"${deal['usd']:,.2f} (`{fmt_amount(deal['coin'], deal['coin_amount'])} {coin}`)", inline=False)
        embed.add_field(name="Receiver received", value=f"${deal['usd']:,.2f} (`{fmt_amount(deal['coin'], amount)} {coin}`)", inline=False)
        embed.add_field(name="Participants", value="Sender: Anonymous\nReceiver: Anonymous", inline=False)
        embed.add_field(name="Deal", value=f"Completed securely • `{deal['id']}`", inline=False)
        embed.add_field(name="Transaction", value=f"[View transaction]({transaction_url(deal['coin'], txid)})", inline=False)
        message = None
    if not any(field.name == "Privacy" for field in embed.fields):
        embed.add_field(name="Privacy", value="Participants: choose whether your name is shown.", inline=False)
    txid = (deal.get("result") or {}).get("txid", deal.get("pending_txid"))
    if not txid:
        return False
    url = transaction_url(deal["coin"], txid)
    view = DealPrivacyView(deal["channel_id"], url, include_close=False)
    message = await message.edit(embed=embed, view=view) if message else await channel.send(embed=embed, view=view)
    deal["completed_crypto_message_id"] = message.id
    deal["privacy_message_id"] = message.id
    deal["privacy_message_in_ticket"] = False
    deal.setdefault("publicity", {})
    store.save(deal)
    return True


async def announce_pending_payout(channel, deal, txid, amount, status_message=None):
    """Lock an accepted payout and immediately show its transaction and close option."""
    deal["stage"] = "payout_pending"
    deal["pending_txid"] = txid
    deal["pending_amount"] = amount
    deal["close_at"] = time.time() + 5 * 60
    store.save(deal)

    config = load_config()
    coin = coin_of(deal)
    tx_url = transaction_url(deal["coin"], txid)
    e = E(
        config,
        "⏳ • Payout Sent",
        "The network accepted the payout request. Its confirmation is still pending, so this is not marked complete yet. "
        "The payout is locked to prevent sending twice. You can close this ticket now; the transcript will be saved for staff.",
        "warning",
    )
    e.add_field(name="Amount", value=f"{fmt_amount(deal['coin'], amount)} {coin}", inline=True)
    e.add_field(name="Transaction", value=f"[View transaction]({tx_url})\n`{txid}`", inline=False)
    try:
        if deal.get("payout_to") != "sender":
            if status_message:
                await prompt_deal_visibility(channel, deal, tx_url, message=status_message)
            else:
                await prompt_deal_visibility(channel, deal, tx_url)
        else:
            if status_message:
                await status_message.edit(embed=e, view=CloseTicketView(tx_url))
            else:
                await channel.send(embed=e, view=CloseTicketView(tx_url))
    except Exception as exc:
        print(f"[payout] deal {deal['id']}: could not post pending status: {exc}")

    log = channel.guild.get_channel(config["channels"].get("deal_logs", 0))
    if log:
        try:
            pending_log = E(config, f"Payout Pending • Deal {deal['id']} ({coin})", None, "warning")
            pending_log.add_field(name="Sender", value=mention(deal["sender_id"]), inline=True)
            pending_log.add_field(name="Receiver", value=mention(deal["receiver_id"]), inline=True)
            pending_log.add_field(name="Transaction", value=f"[View transaction]({tx_url})\n`{txid}`", inline=False)
            await log.send(embed=pending_log)
        except Exception as exc:
            print(f"[payout] deal {deal['id']}: could not post staff log: {exc}")


async def ask_address(channel, deal):
    config = load_config()
    c = coin_of(deal)
    who = "receiver" if deal["payout_to"] == "receiver" else "sender (refund)"
    e = E(config, f"🪙 • What's Your {c} Address?", f"Make sure to paste your correct {c} address.")
    await channel.send(content=f"{mention(payee_id(deal))} ({who})", embed=e, view=AddressView(deal["coin"]))


class AddressModal(discord.ui.Modal):
    def __init__(self, coin):
        super().__init__(title=f"Your {COINS[coin]['label']} Address")
        self.coin = coin
        self.addr = discord.ui.TextInput(label=f"{COINS[coin]['label']} address", max_length=100)
        self.add_item(self.addr)

    async def on_submit(self, interaction: discord.Interaction):
        address = self.addr.value.strip()
        if not validate_address(self.coin, address):
            return await interaction.response.send_message(
                f"❌ That doesn't look like a valid {COINS[self.coin]['label']} address. Try again.", ephemeral=True)
        async with lock_for(interaction.channel_id):
            deal = store.get(interaction.channel_id)
            if not deal or deal["stage"] != "address" or interaction.user.id != payee_id(deal):
                return await interaction.response.send_message("⚠️ Not valid right now.", ephemeral=True)
            deal["stage"], deal["pending_address"] = "address_confirm", address
            store.save(deal)
        e = E(load_config(), "⚠️ • Confirm Address",
              f'Click **"Confirm"** to send {COINS[self.coin]["label"]} or **"Back"** to cancel.', "warning")
        e.add_field(name="Address:", value=f"```{address}```", inline=False)
        await interaction.response.send_message(content=interaction.user.mention, embed=e, view=AddressConfirmView())


class AddressView(discord.ui.View):
    def __init__(self, coin="LTC"):
        super().__init__(timeout=None)
        self.children[0].label = f"Enter Your {COINS[coin]['label']} Address"

    @discord.ui.button(label="Enter Your Address", style=discord.ButtonStyle.primary, custom_id="deal_enter_addr")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        deal = store.get(interaction.channel_id)
        if not deal or deal["stage"] != "address":
            return await interaction.response.send_message("⚠️ That button is no longer active.", ephemeral=True)
        if interaction.user.id != payee_id(deal):
            return await interaction.response.send_message(
                f"❌ Only {mention(payee_id(deal))} can enter the address.", ephemeral=True)
        await interaction.response.send_modal(AddressModal(deal["coin"]))


class AddressConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, custom_id="deal_addr_ok")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with lock_for(interaction.channel_id):
            deal = store.get(interaction.channel_id)
            if not deal or deal["stage"] != "address_confirm":
                return await interaction.response.send_message("⚠️ That button is no longer active.", ephemeral=True)
            if interaction.user.id != payee_id(deal):
                return await interaction.response.send_message("❌ Only the person being paid can confirm.", ephemeral=True)
            deal["stage"] = "paying"  # blocks any second click from paying twice
            store.save(deal)
            await interaction.response.defer()
            sending = E(
                load_config(), "⏳ • Sending Payout",
                "Submitting the payout now. Please don't click again or send another payout.",
                "warning")
            await interaction.message.edit(embed=sending, view=None)
            await do_payout(interaction.channel, deal, interaction.message)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, custom_id="deal_addr_back")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with lock_for(interaction.channel_id):
            deal = store.get(interaction.channel_id)
            if not deal or deal["stage"] != "address_confirm" or interaction.user.id != payee_id(deal):
                return await interaction.response.send_message("⚠️ Not valid right now.", ephemeral=True)
            deal["stage"], deal["pending_address"] = "address", None
            store.save(deal)
            await interaction.response.edit_message(view=None)
            await ask_address(interaction.channel, deal)


async def do_payout(channel, deal, status_message=None):
    config = load_config()
    wallet = get_wallet(config)
    refund = deal["payout_to"] == "sender"
    amount = deal["received"] if refund else deal["payout_amount"]
    try:
        # Only sweep the service-fee remainder to the treasury when the sender paid (about) the right amount;
        # an overpayment stays in the deal address for staff to sort out.
        sweep = (not refund) and deal["received"] <= deal["coin_amount"] * 1.01
        res = await wallet.send(deal, deal["pending_address"], amount, sweep)
        if res.get("confirmation_pending"):
            await announce_pending_payout(channel, deal, res["txid"], res["sent"], status_message)
            return
    except WalletPendingError as e:
        # The chain may still confirm this transaction. Never let the user retry
        # the payout while its result is uncertain.
        await announce_pending_payout(channel, deal, e.txid, amount, status_message)
        return
    except Exception as e:  # leave the deal recoverable and tell staff
        print(f"[payout] deal {deal['id']} failed: {e}")
        deal["stage"] = "address"
        store.save(deal)
        error_message = f"❌ The withdrawal failed: `{e}`\nNothing was sent. Please try entering the address again or ping staff."
        if status_message:
            await status_message.edit(content=error_message, embed=None, view=None)
        else:
            await channel.send(error_message)
        await ask_address(channel, deal)
        return

    deal["stage"], deal["result"] = "done", {"txid": res["txid"], "sent": res["sent"], "to": deal["pending_address"],
                                              "refund": refund}
    store.save(deal)

    c = coin_of(deal)
    txid = res["txid"]
    tx_url = transaction_url(deal["coin"], txid)
    deal["close_at"] = time.time() + 5 * 60
    store.save(deal)
    if not refund:
        if status_message:
            await prompt_deal_visibility(channel, deal, tx_url, message=status_message)
        else:
            await prompt_deal_visibility(channel, deal, tx_url)
    else:
        e = E(config, "✅ • Refund Complete", None, "success")
        e.add_field(name="Deal amount", value=f"${deal['usd']:,.2f} (`{fmt_amount(deal['coin'], deal['coin_amount'])} {c}`)", inline=False)
        e.add_field(name="Sender refunded", value=f"`{fmt_amount(deal['coin'], res['sent'])} {c}`", inline=False)
        e.add_field(name="Transaction", value=f"[View transaction]({tx_url})", inline=False)
        if status_message:
            await status_message.edit(content=mention(payee_id(deal)), embed=e, view=CloseTicketView(tx_url))
        else:
            await channel.send(content=mention(payee_id(deal)), embed=e, view=CloseTicketView(tx_url))

    guild = channel.guild
    log = guild.get_channel(config["channels"].get("deal_logs", 0))
    if log:
        lg = E(config, f"Deal {deal['id']} {'REFUNDED' if refund else 'COMPLETED'} ({c})", None, "accent")
        lg.add_field(name="Sender", value=mention(deal["sender_id"]), inline=True)
        lg.add_field(name="Receiver", value=mention(deal["receiver_id"]), inline=True)
        lg.add_field(name="USD", value=f"${deal['usd']:,.2f}", inline=True)
        lg.add_field(name="Paid to", value=f"`{deal['pending_address']}`", inline=False)
        lg.add_field(name="Txid", value=f"`{txid}`", inline=False)
        await log.send(embed=lg)


# ==================== COG ====================

class AutoDeal(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._cleaned_completed_posts = False
        self.watcher.start()

    def cog_unload(self):
        self.watcher.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._cleaned_completed_posts:
            return
        self._cleaned_completed_posts = True
        config = load_config()
        channel = self.bot.get_channel(config["channels"].get("completed_crypto", 0))
        if channel is None:
            return
        try:
            async for message in channel.history(limit=200):
                if message.author.id != self.bot.user.id or not message.embeds:
                    continue
                original = message.embeds[0]
                if not original.title or "Trade Complete" not in original.title:
                    continue
                updated = discord.Embed.from_dict(original.to_dict())
                status_indexes = [i for i, field in enumerate(updated.fields) if field.name == "Status"]
                for index in reversed(status_indexes):
                    updated.remove_field(index)
                if status_indexes:
                    await message.edit(embed=updated)
        except discord.Forbidden:
            print("[completed-crypto] cannot update old cards; missing message history or edit permission")
        except Exception as exc:
            print(f"[completed-crypto] old card cleanup failed: {exc}")

    @tasks.loop(seconds=5)
    async def watcher(self):
        config = load_config()
        wallet = get_wallet(config)
        for deal in store.all_deals():
            guild_id = config.get("guild_id")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            if guild is None and len(self.bot.guilds) == 1:
                guild = self.bot.guilds[0]
            channel = self.bot.get_channel(deal["channel_id"])
            if channel is None:
                if (guild and deal.get("stage") in {"done", "payout_pending"}
                        and deal.get("payout_to") != "sender" and not deal.get("privacy_finalized")):
                    if not deal.get("stats_recorded"):
                        totals = record_completed_deal(deal)
                        await sync_rank_roles(guild, totals)
                        deal["stats_recorded"] = True
                    if deal.get("completed_crypto_posted"):
                        if deal.get("privacy_message_id"):
                            continue
                        if await attach_legacy_privacy_choices(guild, config, deal):
                            continue
                    choices = deal.setdefault("publicity", {})
                    choices.setdefault(str(deal["sender_id"]), False)
                    choices.setdefault(str(deal["receiver_id"]), False)
                    store.save(deal)
                    result = deal.get("result") or {}
                    txid = result.get("txid", deal.get("pending_txid"))
                    if txid:
                        await post_completed_crypto(
                            guild, config, deal,
                            result.get("sent", deal.get("pending_amount", 0)), txid)
                continue
            guild = channel.guild
            if deal["stage"] in {"done", "payout_pending"}:
                if (deal.get("payout_to") != "sender" and not deal.get("privacy_finalized")
                        and not deal.get("privacy_message_id")):
                    result = deal.get("result") or {}
                    txid = result.get("txid", deal.get("pending_txid"))
                    if txid:
                        if deal.get("completed_crypto_posted"):
                            deal["close_at"] = time.time() + 5 * 60
                            store.save(deal)
                        await prompt_deal_visibility(
                            channel, deal, transaction_url(deal["coin"], txid))
                if not deal.get("close_at"):
                    # Preserve the five-minute close timer for completed and
                    # broadcast-but-unconfirmed payouts alike.
                    deal["close_at"] = time.time() + 5 * 60
                    store.save(deal)
                elif time.time() >= deal["close_at"]:
                    try:
                        if deal["stage"] in {"done", "payout_pending"} and not deal.get("privacy_finalized"):
                            if deal.get("payout_to") != "sender" and not deal.get("stats_recorded"):
                                totals = record_completed_deal(deal)
                                await sync_rank_roles(guild, totals)
                                deal["stats_recorded"] = True
                            choices = deal.setdefault("publicity", {})
                            choices.setdefault(str(deal["sender_id"]), False)
                            choices.setdefault(str(deal["receiver_id"]), False)
                            store.save(deal)
                            result = deal.get("result") or {}
                            txid = result.get("txid", deal.get("pending_txid"))
                            if txid:
                                await post_completed_crypto(
                                    guild, config, deal,
                                    result.get("sent", deal.get("pending_amount", 0)), txid)
                        reason = "automatic close after payout submission" if deal["stage"] == "payout_pending" else "automatic close after 5 minutes"
                        await close_ticket_channel(channel, reason)
                    except Exception as e:
                        print(f"[auto-close] deal {deal.get('id')}: {e}")
                continue
            if deal["stage"] != "awaiting_payment":
                continue
            try:
                async with lock_for(deal["channel_id"]):
                    deal = store.get(deal["channel_id"])
                    if not deal or deal["stage"] != "awaiting_payment":
                        continue
                    if deal["coin"] == "LTC":
                        # One explorer lookup returns both confirmed and mempool
                        # outputs, so the pending notice does not wait for a
                        # second sequential API request.
                        seen, pending = await wallet.get_ltc_received_state(deal)
                    else:
                        seen = await wallet.get_received(deal)
                        pending = await wallet.get_unconfirmed_received(deal) if not seen else None
                    # 1% tolerance so tiny rounding differences don't block a deal
                    if seen and seen["amount"] >= deal["coin_amount"] * 0.99:
                        deal["stage"], deal["received"] = "funded", seen["amount"]
                        deal["deposit_txid"] = seen.get("txid")
                        store.save(deal)
                        await channel.send(
                            content=f"{mention(deal['sender_id'])} {mention(deal['receiver_id'])}",
                            embed=funded_embed(config, deal), view=FundedView())
                    else:
                        pending_total = (pending["amount"] if pending else 0) + (seen["amount"] if seen else 0)
                        if pending and pending_total >= deal["coin_amount"] * 0.99:
                            if not deal.get("pending_deposit_notified"):
                                deal["pending_deposit_notified"] = True
                                deal["pending_deposit_txid"] = pending.get("txid")
                                store.save(deal)
                                confirmations = getattr(wallet, "ltc_conf", 2)
                                e = E(
                                    config,
                                    "⏳ • Litecoin Transaction Detected",
                                    f"The bot detected **{fmt_amount(deal['coin'], pending_total)} LTC** at this deal address.\n\n"
                                    f"The transaction is not confirmed yet. Waiting for **{confirmations} confirmations**; "
                                    "the deal will become funded only after the required confirmations are reached.",
                                    "warning",
                                )
                                if pending.get("txid"):
                                    e.add_field(name="Deposit transaction", value=f"`{pending['txid']}`", inline=False)
                                await channel.send(
                                    content=f"{mention(deal['sender_id'])} {mention(deal['receiver_id'])}",
                                    embed=e)
                        if time.time() <= deal["expires"]:
                            continue
                        if deal.get("pending_deposit_notified"):
                            if not deal.get("pending_expiry_notified"):
                                deal["pending_expiry_notified"] = True
                                store.save(deal)
                                await channel.send(
                                    "⚠️ This LTC payment was detected but did not reach the required confirmations "
                                    "within the 45-minute payment window. The ticket is staying open for staff review; "
                                    "do not send it again."
                                )
                            continue
                        deal["stage"] = "expired"
                        store.save(deal)
                        await channel.send("⏰ No confirmed payment was detected within 45 minutes. Closing this ticket.")
                        await asyncio.sleep(5)
                        await channel.delete(reason="AutoMM: payment window expired")
            except Exception as e:
                print(f"[watcher] deal {deal.get('id')}: {e}")

    @watcher.before_loop
    async def before_watcher(self):
        await self.bot.wait_until_ready()

    @commands.command(name="simpay")
    @commands.has_permissions(administrator=True)
    async def simpay(self, ctx):
        """DRY RUN only: pretend the sender paid the full amount in this ticket."""
        wallet = get_wallet(load_config())
        if getattr(wallet, "mode", "") != "dryrun":
            return await ctx.send("❌ `.simpay` only works in dry-run mode.")
        deal = store.get(ctx.channel.id)
        if not deal or deal["stage"] != "awaiting_payment":
            return await ctx.send("❌ This ticket isn't waiting for a payment.")
        wallet.simulate_payment(deal["address"], deal["coin_amount"])
        await ctx.send("🧪 Simulated payment. The bot should pick it up within ~15 seconds.")


async def setup(bot):
    wallet = get_wallet(load_config())  # fails fast on boot if the live wallet is misconfigured
    print(f"💰 Wallet mode: {wallet.mode}")
    await bot.add_cog(AutoDeal(bot))
    for view in (RoleView(), RolesConfirmView(), SetAmountView(), AmountConfirmView(), CopyDetailsView(),
                 FundedView(), CancelConfirmView(), AddressView(), AddressConfirmView()):
        bot.add_view(view)
    for deal in store.all_deals():
        if (deal.get("stage") in {"done", "payout_pending"} and deal.get("payout_to") != "sender"
                and not deal.get("privacy_finalized") and deal.get("privacy_message_id")):
            txid = (deal.get("result") or {}).get("txid", deal.get("pending_txid"))
            if txid:
                bot.add_view(DealPrivacyView(
                    deal["channel_id"], transaction_url(deal["coin"], txid),
                    include_close=deal.get("privacy_message_in_ticket", True)),
                             message_id=deal["privacy_message_id"])
