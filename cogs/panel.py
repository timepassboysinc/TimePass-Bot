import discord
from discord.ext import commands
from discord import app_commands
import json
import io
import os
from datetime import datetime
from urllib.parse import urlparse
from utils.embeds import get_color

def load_config():
    with open("config.json", "r") as f:
        return json.load(f)

def load_panels():
    if not os.path.exists("data/panels.json"):
        os.makedirs("data", exist_ok=True)
        default = {
            "auto": {
                "title": "Time AutoMM",
                "description": "• Paid Service\n• Read our ToS before using the bot: #tos-crypto",
                "fees": "• Deals $250+: $1.50\n• Deals under $250: $0.50\n• Deals under $50 are **FREE**",
                "ltc_label": "Request LTC",
                "sol_label": "Request SOL",
                "ltc_emoji": "",
                "sol_emoji": ""
            },
            "human": {
                "title": "Human Middleman Service",
                "description": "Need a real person to middleman your trade?\n\nClick the button below to open a ticket.\nPerfect for high-value or complicated deals.",
                "button_label": "Request Human Middleman",
                "button_emoji": ""
            },
            "nitro": {
                "title": "__**✨ DISCORD NITRO**__",
                "description": "🌟 Premium perks, better chats, and a smoother Discord experience.\n\nChoose your Nitro plan in the ticket; our team will confirm the details with you.",
                "button_label": "Order Nitro",
                "button_emoji": "",
                "basic_price": "Ask for price",
                "basic_stock": "Ask for stock",
                "premium_price": "Ask for price",
                "premium_stock": "Ask for stock",
                "image_url": ""
            },
            "robux": {
                "title": "__**💎 ROBUX STORE**__",
                "description": "✨ Fast, reliable Robux orders with smooth delivery.\n\nChoose your quantity in the ticket and our team will confirm your order.",
                "button_label": "Order Robux",
                "button_emoji": "",
                "price": "$4.50 per 1,000 Robux",
                "stock": "Ask for current stock",
                "image_url": ""
            },
            "slots": {
                "title": "__**🎟️ SLOTS FOR BUYING / SELLING**__",
                "description": (
                    "💵 Slots for buying and selling stuff\n\n"
                    "📦 **PACK 1 — $2**\n• 10 days\n• 1 @everyone ping\n\n"
                    "📦 **PACK 2 — $6**\n• 30 days\n• 3 @everyone pings\n\n"
                    "✨ **EXTRAS**\n• @everyone ping — $1\n• @here ping — $0.50\n• 7-day extension — $1"
                ),
                "button_label": "Order Slots",
                "button_emoji": "",
                "image_url": ""
            },
            "boosts": {
                "title": "Server Boosts",
                "description": "Buy Server Boosts safely and fast.\n\nClick the button below to open an order ticket.\nRead #tos-shop before ordering.",
                "button_label": "Buy Server Boosts",
                "button_emoji": ""
            }
        }
        with open("data/panels.json", "w") as f:
            json.dump(default, f, indent=2)
        return default
    with open("data/panels.json", "r") as f:
        panels = json.load(f)
    if "slots" not in panels:
        panels["slots"] = {
            "title": "__**🎟️ SLOTS FOR BUYING / SELLING**__",
            "description": (
                "💵 Slots for buying and selling stuff\n\n"
                "📦 **PACK 1 — $2**\n• 10 days\n• 1 @everyone ping\n\n"
                "📦 **PACK 2 — $6**\n• 30 days\n• 3 @everyone pings\n\n"
                "✨ **EXTRAS**\n• @everyone ping — $1\n• @here ping — $0.50\n• 7-day extension — $1"
            ),
            "button_label": "Order Slots",
            "button_emoji": "",
            "image_url": ""
        }
        save_panels(panels)
    return panels

def save_panels(data):
    os.makedirs("data", exist_ok=True)
    with open("data/panels.json", "w") as f:
        json.dump(data, f, indent=2)


def parse_button_emoji(raw_emoji):
    """Parse an optional custom emoji safely; invalid or empty values disable it."""
    raw_emoji = (raw_emoji or "").strip()
    if not raw_emoji:
        return None
    try:
        emoji = discord.PartialEmoji.from_str(raw_emoji)
        return emoji if emoji.id else None
    except (ValueError, TypeError):
        return None


# ==================== VIEWS ====================

class AutoMMView(discord.ui.View):
    def __init__(self, only_coin=None):
        super().__init__(timeout=None)
        panels = load_panels()
        auto = panels.get("auto", {})
        config = load_config()
        enabled = config.get("crypto", {})
        coin_flags = {"auto_ltc": ("LTC", "ltc"), "auto_usdt": ("USDT", "usdt_bep20"),
                      "auto_sol": ("SOL", "sol")}
        for button in list(self.children):
            coin, flag = coin_flags[button.custom_id]
            if not enabled.get(flag, False) or (only_coin and coin != only_coin):
                self.remove_item(button)
        # Update labels for whichever coin buttons are enabled.
        for button, label_key, emoji_key, default_label in [
            ("auto_ltc", "ltc_label", "ltc_emoji", "Request LTC"),
            ("auto_usdt", "usdt_label", "usdt_emoji", "Request USDT [BEP-20]"),
            ("auto_sol", "sol_label", "sol_emoji", "Request SOL"),
        ]:
            button = next((item for item in self.children if item.custom_id == button), None)
            if not button:
                continue
            button.label = auto.get(label_key, default_label)
            raw_emoji = (auto.get(emoji_key) or "").strip()
            try:
                button.emoji = discord.PartialEmoji.from_str(raw_emoji) if raw_emoji else None
            except (ValueError, TypeError):
                button.emoji = None

    @discord.ui.button(label="Request LTC", style=discord.ButtonStyle.primary, custom_id="auto_ltc")
    async def ltc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "LTC")

    @discord.ui.button(label="Request USDT [BEP-20]", style=discord.ButtonStyle.success, custom_id="auto_usdt")
    async def usdt(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "USDT (BEP-20)")

    @discord.ui.button(label="Request SOL", style=discord.ButtonStyle.secondary, custom_id="auto_sol")
    async def sol(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "SOL")

    async def create_ticket(self, interaction: discord.Interaction, crypto: str):
        # Opens the "Fill out the format" form; the ticket + deal are created in cogs/autodeal.py
        from cogs.autodeal import DealFormModal
        config = load_config()
        coin = {"LTC": "ltc", "SOL": "sol"}.get(crypto, "usdt_bep20")
        if not config.get("crypto", {}).get(coin, False):
            return await interaction.response.send_message("❌ That coin is temporarily unavailable.", ephemeral=True)
        role = interaction.guild.get_role(config["roles"].get("blacklisted", 0))
        if role and role in interaction.user.roles:
            return await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
        await interaction.response.send_modal(DealFormModal({"LTC": "LTC", "SOL": "SOL"}.get(crypto, "USDT")))


class HumanMMView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        panels = load_panels()
        human = panels.get("human", {})
        self.children[0].label = human.get("button_label", "Request Human Middleman")
        self.children[0].emoji = parse_button_emoji(human.get("button_emoji", ""))

    @discord.ui.button(label="Request Human Middleman", style=discord.ButtonStyle.primary, custom_id="human_mm")
    async def request(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = load_config()
        role = interaction.guild.get_role(config["roles"].get("blacklisted", 0))
        if role and role in interaction.user.roles:
            return await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
        await interaction.response.send_modal(HumanMMRequestModal())


class HumanMMRequestModal(discord.ui.Modal, title="Fill out the format"):
    trader = discord.ui.TextInput(
        label="Paste Your Trader's Username or ID",
        placeholder="e.g.: username / 123456789012345678", max_length=40)
    giving = discord.ui.TextInput(label="What are You giving?", style=discord.TextStyle.paragraph, max_length=300)
    trader_giving = discord.ui.TextInput(
        label="What is Your Trader giving?", style=discord.TextStyle.paragraph, max_length=300)

    async def on_submit(self, interaction: discord.Interaction):
        config = load_config()
        guild = interaction.guild
        category = guild.get_channel(config["categories"].get("human_tickets", 0))
        if not category:
            return await interaction.response.send_message("❌ `human_tickets` category ID is missing in config.json", ephemeral=True)

        trader = await resolve_human_trader(guild, self.trader.value)
        if not trader or trader.bot or trader.id == interaction.user.id:
            return await interaction.response.send_message("❌ I couldn't find a different human trader in this server. Use their exact username or ID.", ephemeral=True)

        bot_member = guild.me or guild.get_member(interaction.client.user.id)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True),
            trader: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True),
        }
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True)

        staff = guild.get_role(config["roles"].get("staff", 0))
        admin = guild.get_role(config["roles"].get("admin", 0))
        for role in (staff, admin):
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)

        ticket = await category.create_text_channel(
            name=f"mm-{interaction.user.name[:12]}", overwrites=overwrites,
            topic=f"HumanMM|{interaction.user.id}|{trader.id}|0")

        embed = discord.Embed(title="Human Middleman Request",
            description=f"Welcome {interaction.user.mention} and {trader.mention}! A middleman will claim this ticket. After it is claimed, only the two traders, the assigned middleman, and admins can view it.",
            color=get_color(config, "accent"), timestamp=datetime.utcnow())
        embed.add_field(name="What the requester is giving", value=self.giving.value, inline=False)
        embed.add_field(name="What the trader is giving", value=self.trader_giving.value, inline=False)
        embed.set_footer(text=config.get("footer", "Time AutoMM"))
        staff_mention = staff.mention if staff else "Staff"
        await ticket.send(content=f"{interaction.user.mention} {trader.mention} | {staff_mention}", embed=embed, view=HumanMMClaimView())
        await ticket.send(view=CloseTicketView())

        for key in ("ticket_logs", "deal_logs"):
            ch = guild.get_channel(config["channels"].get(key, 0))
            if ch:
                log = discord.Embed(title="New Human MM Ticket", color=get_color(config, "warning"), timestamp=datetime.utcnow())
                log.add_field(name="Requester", value=f"{interaction.user.mention} (`{interaction.user.id}`)")
                log.add_field(name="Trader", value=f"{trader.mention} (`{trader.id}`)")
                log.add_field(name="Ticket", value=ticket.mention)
                await ch.send(embed=log)
        await interaction.response.send_message(f"✅ Ticket created: {ticket.mention}", ephemeral=True)


async def resolve_human_trader(guild, raw):
    raw = (raw or "").strip().lstrip("@")
    import re
    match = re.fullmatch(r"<@!?(\d+)>", raw)
    user_id = int(match.group(1)) if match else (int(raw) if raw.isdigit() else None)
    if user_id:
        member = guild.get_member(user_id)
        if member:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.NotFound:
            return None
    name = raw.casefold()
    return discord.utils.find(lambda member: member.name.casefold() == name or member.display_name.casefold() == name, guild.members)


class HumanMMClaimView(discord.ui.View):
    def __init__(self, claimed=False):
        super().__init__(timeout=None)
        self.claim.disabled = claimed
        if claimed:
            self.claim.label = "Ticket Claimed"

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.success, custom_id="human_mm_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = load_config()
        staff_role = interaction.guild.get_role(config["roles"].get("staff", 0))
        admin_role = interaction.guild.get_role(config["roles"].get("admin", 0))
        if not ((staff_role and staff_role in interaction.user.roles) or (admin_role and admin_role in interaction.user.roles)):
            return await interaction.response.send_message("Only staff can claim this ticket.", ephemeral=True)
        channel = interaction.channel
        parts = (channel.topic or "").split("|")
        if len(parts) < 4 or parts[0] != "HumanMM":
            return await interaction.response.send_message("This is not an active human MM request.", ephemeral=True)
        if parts[3] != "0":
            return await interaction.response.send_message("This ticket has already been claimed.", ephemeral=True)

        # The channel topic is the durable claim record; serialize competing clicks.
        lock = _human_claim_locks.setdefault(channel.id, __import__("asyncio").Lock())
        async with lock:
            parts = (channel.topic or "").split("|")
            if len(parts) < 4 or parts[3] != "0":
                return await interaction.response.send_message("This ticket has already been claimed.", ephemeral=True)
            overwrites = dict(channel.overwrites)
            if staff_role:
                # Explicitly deny the staff role so a category-level allow cannot
                # accidentally keep every middleman in the claimed ticket.
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=False)
            overwrites[interaction.user] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True, embed_links=True,
                read_message_history=True, manage_messages=True)
            await channel.edit(overwrites=overwrites, topic=f"HumanMM|{parts[1]}|{parts[2]}|{interaction.user.id}")
            await interaction.response.edit_message(view=HumanMMClaimView(claimed=True))
            await channel.send(f"✅ {interaction.user.mention} claimed this ticket. Other middlemen can no longer view it.")


_human_claim_locks = {}


class CloseTicketView(discord.ui.View):
    def __init__(self, transaction_url=None):
        super().__init__(timeout=None)
        if transaction_url:
            self.add_item(discord.ui.Button(label="View Transaction", style=discord.ButtonStyle.link,
                                            url=transaction_url))

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await close_ticket_channel(interaction.channel, f"{interaction.user} ({interaction.user.id})", interaction)


async def close_ticket_channel(channel, closed_by, interaction=None):
    config = load_config()
    messages = []
    try:
        async for msg in channel.history(limit=None, oldest_first=True):
            t = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            content = msg.content or ""
            for embed in msg.embeds:
                content += f"\n[Embed] {embed.title or ''}\n{embed.description or ''}"
                for field in embed.fields:
                    content += f"\n{field.name}: {field.value}"
            for attachment in msg.attachments:
                content += f"\n[Attachment] {attachment.filename}: {attachment.url}"
            messages.append(f"[{t}] {msg.author} ({msg.author.id}): {content}")
    except Exception as exc:
        print(f"[transcript] Could not read #{channel.name}: {exc}")
        if interaction:
            await interaction.followup.send(
                "I couldn't collect the full transcript, so I left this ticket open. Please ask staff to close it.",
                ephemeral=True)
        return False

    text = f"Transcript of #{channel.name}\nClosed by: {closed_by}\n"
    text += f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n{'='*50}\n\n" + "\n".join(messages)
    filename = f"transcript-{channel.name}.txt"
    # Keep each upload comfortably under Discord's per-file limit, even for
    # long transcripts with non-ASCII text.
    parts = [text[i:i + 1_000_000] for i in range(0, len(text), 1_000_000)] or [""]
    log_channels = []
    for key in ("ticket_logs", "deal_logs"):
        log = channel.guild.get_channel(config["channels"].get(key, 0))
        if log and all(log.id != existing.id for existing in log_channels):
            log_channels.append(log)
    saved = False
    last_error = None
    for log_channel in log_channels:
        try:
            for index, part in enumerate(parts, start=1):
                file = discord.File(
                    fp=io.BytesIO(part.encode("utf-8")),
                    filename=filename if len(parts) == 1 else f"{filename[:-4]}-{index}.txt")
                if index == 1:
                    emb = discord.Embed(
                        title="Ticket Closed",
                        description=f"**Channel:** `{channel.name}`\n**Closed by:** {closed_by}",
                        color=get_color(config, "error"), timestamp=datetime.utcnow())
                    await log_channel.send(embed=emb, file=file)
                else:
                    await log_channel.send(file=file)
            saved = True
            break
        except Exception as exc:
            last_error = exc
            print(f"[transcript] Could not save to #{getattr(log_channel, 'name', 'log')}: {exc}")

    if not saved:
        if interaction:
            await interaction.followup.send(
                "I couldn't save the transcript, so I left this ticket open. Staff should check the ticket log channel permissions.",
                ephemeral=True)
        if last_error is None:
            print(f"[transcript] No ticket_logs or deal_logs channel is configured for #{channel.name}")
        return False

    if interaction:
        await interaction.followup.send("Ticket closed. Transcript saved for staff.", ephemeral=True)
    await channel.delete(reason=f"Closed by {closed_by}")
    return True


# ==================== COG ====================

class Panel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def make_auto_embed(self):
        config = load_config()
        panels = load_panels()
        auto = panels.get("auto", {})

        embed = discord.Embed(
            title=auto.get("title", "Time AutoMM"),
            color=get_color(config, "main"),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="\u200b", value=auto.get("description", "• Paid Service\n• Read our ToS before using the bot: #tos-crypto"), inline=False)
        embed.add_field(name="**Fees:**", value=auto.get("fees", "• Deals $250+: $1.50\n• Deals under $250: $0.50\n• Deals under $50 are **FREE**"), inline=False)
        embed.set_footer(text=config.get("footer", "Time AutoMM • Secure Middleman"))
        return embed

    def make_coin_embed(self, coin):
        config = load_config()
        names = {"LTC": ("Litecoin", "Litecoin Mainnet", "accent"),
                 "SOL": ("Solana", "Solana Mainnet", "success"),
                 "USDT": ("USDT [BEP-20]", "BNB Smart Chain (BEP-20)", "success")}
        name, network, color = names[coin]
        icon = {"LTC": "Ł", "SOL": "◎", "USDT": "₮"}[coin]
        embed = discord.Embed(
            title=f"{icon} • Request {name} • {icon}",
            description=f"Open a secure {name} middleman ticket.",
            color=get_color(config, color),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Network", value=network, inline=False)
        embed.set_footer(text=config.get("footer", "Time AutoMM • Secure Middleman"))
        return embed

    async def post_auto_panel(self, channel):
        config = load_config()
        await channel.send(embed=self.make_auto_embed())
        for coin, flag in (("LTC", "ltc"), ("SOL", "sol"), ("USDT", "usdt_bep20")):
            if config.get("crypto", {}).get(flag, False):
                await channel.send(embed=self.make_coin_embed(coin), view=AutoMMView(only_coin=coin))

    # ===== SEND COMMANDS =====
    @app_commands.command(name="sendautopanel", description="Send Auto MM panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def sendautopanel(self, interaction: discord.Interaction):
        try:
            await self.post_auto_panel(interaction.channel)
            await interaction.response.send_message("✅ Auto MM panel sent!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: `{e}`", ephemeral=True)

    @app_commands.command(name="sendhumanpanel", description="Send Human MM panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def sendhumanpanel(self, interaction: discord.Interaction):
        config = load_config()
        panels = load_panels()
        h = panels.get("human", {})
        embed = discord.Embed(title=h.get("title", "Human Middleman Service"), description=h.get("description", ""), color=get_color(config, "main"), timestamp=datetime.utcnow())
        embed.set_footer(text=config.get("footer", "Time AutoMM"))
        await interaction.channel.send(embed=embed, view=HumanMMView())
        await interaction.response.send_message("✅ Human MM panel sent!", ephemeral=True)

    # Prefix versions
    @commands.command(name="sendautopanel")
    @commands.has_permissions(administrator=True)
    async def sendautopanel_p(self, ctx):
        try:
            await self.post_auto_panel(ctx.channel)
        except Exception as e:
            await ctx.send(f"❌ Error: `{e}`")

    @commands.command(name="sendhumanpanel")
    @commands.has_permissions(administrator=True)
    async def sendhumanpanel_p(self, ctx):
        config = load_config()
        panels = load_panels()
        h = panels.get("human", {})
        embed = discord.Embed(title=h.get("title", "Human Middleman Service"), description=h.get("description", ""), color=get_color(config, "main"))
        embed.set_footer(text=config.get("footer", "Time AutoMM"))
        await ctx.send(embed=embed, view=HumanMMView())

    # ===== EDIT COMMANDS =====
    @commands.command(name="editpanel")
    @commands.has_permissions(administrator=True)
    async def editpanel(self, ctx, panel: str = None, field: str = None, *, value: str = None):
        """
        Edit panel text.
        Usage:
        .editpanel auto title New Title Here
        .editpanel auto description New description...
        .editpanel auto fees New fees text...
        .editpanel auto ltc_label New LTC Button
        .editpanel human title ...
        .editpanel nitro button_label Buy Nitro Now
        """
        if not panel or not field or not value:
            return await ctx.send(
                "**Usage:**\n"
                "`.editpanel <panel> <field> <new text>`\n\n"
                "**Panels:** `auto` `human` `nitro` `robux` `slots` `boosts`\n"
                "**Auto fields:** `title` `description` `fees` `ltc_label` `sol_label` `ltc_emoji` `sol_emoji`\n"
                "**Human/Nitro/Robux/Slots/Boosts fields:** `title` `description` `button_label` `button_emoji` (Nitro, Robux, Slots, and Boosts support `image_url`; Robux supports `price` and `stock`; Nitro supports `basic_price`, `basic_stock`, `premium_price`, and `premium_stock`)\n"
                "For a custom emoji, use its Discord markup, e.g. `<:name:123456789012345678>`. Use `none` to clear it.\n"
                "For Nitro, Robux, Slots, or Boosts images, upload the image to Discord, copy its media link, then use `.editpanel <panel> image_url <link>`; use `none` to clear it. Nitro, Robux, and Slots edits update their latest posted panels in place.\n"
                "**Nitro examples:** `.editpanel nitro basic_price $X per month`, `.editpanel nitro premium_price $Y per month`, `.editpanel nitro basic_stock 10 available`, `.editpanel nitro premium_stock 5 available`.\n"
                "**Other examples:** `.editpanel nitro button_emoji <:nitro:123456789012345678>` or `.editpanel nitro image_url none`"
            )

        panels = load_panels()
        panel = panel.lower()
        if panel not in panels:
            return await ctx.send("❌ Invalid panel. Use: auto, human, nitro, robux, slots, boosts")

        if field not in panels[panel]:
            return await ctx.send(f"❌ Invalid field for `{panel}`.")

        if field.endswith("_emoji"):
            value = "" if value.lower() in {"none", "off", "clear"} else value.strip()
            if value and not (value.startswith("<:") or value.startswith("<a:")):
                return await ctx.send("For custom emojis, use Discord markup like `<:name:123456789012345678>`, or use `none` to clear the emoji.")
            if value:
                try:
                    parsed = discord.PartialEmoji.from_str(value)
                    if not parsed.id:
                        raise ValueError("Not a custom emoji")
                except (ValueError, TypeError):
                    return await ctx.send("That custom emoji format is invalid. Copy the emoji as a Discord emoji mention and try again.")
        if field == "image_url":
            value = "" if value.lower() in {"none", "off", "clear"} else value.strip()
            if value:
                parsed = urlparse(value)
                if parsed.scheme != "https" or not parsed.netloc:
                    return await ctx.send("Use the HTTPS media link copied from your Discord image upload, or use `none` to clear it.")
        panels[panel][field] = value
        save_panels(panels)
        if panel in {"nitro", "robux", "slots"}:
            shop = self.bot.get_cog("Shop")
            refreshed = await shop.refresh_saved_panel(panel, ctx.guild) if shop and ctx.guild else False
            panel_label = {"nitro": "Nitro", "robux": "Robux", "slots": "Slots"}[panel]
            status = f"The {panel_label} panel was updated in place." if refreshed else f"Run `.send{panel}panel` once in the panel channel to create the live-updating post."
            return await ctx.send(f"✅ Updated `{panel}.{field}` to:\n```{value}```\n\n{status}")
        await ctx.send(f"✅ Updated `{panel}.{field}` to:\n```{value}```\n\nRe-send the panel to see the change.")


async def setup(bot):
    await bot.add_cog(Panel(bot))
    bot.add_view(AutoMMView())
    bot.add_view(HumanMMView())
    bot.add_view(HumanMMClaimView())
    bot.add_view(CloseTicketView())
