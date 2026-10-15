import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from datetime import datetime
from utils.embeds import get_color
from cogs.panel import CloseTicketView, load_panels, parse_button_emoji

PANEL_MESSAGES_FILE = "data/panel_messages.json"

def load_config():
    with open("config.json", "r") as f:
        return json.load(f)


async def create_shop_ticket(interaction, product: str):
    config = load_config()
    user = interaction.user
    guild = interaction.guild

    role = guild.get_role(config["roles"].get("blacklisted", 0))
    if role and role in user.roles:
        return await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)

    category = guild.get_channel(config["categories"].get("shop_tickets", 0))
    if not category:
        return await interaction.response.send_message("❌ `shop_tickets` category ID is missing in config.json", ephemeral=True)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True)
    }
    for rname in ["staff", "admin"]:
        r = guild.get_role(config["roles"].get(rname, 0))
        if r:
            overwrites[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)

    ticket = await category.create_text_channel(
        name=f"order-{product.lower().replace(' ', '-')}-{user.name[:10]}",
        overwrites=overwrites,
        topic=f"Shop|{product}|{user.id}"
    )

    order_steps = (
        "Choose a plan: **Nitro Basic** or **Nitro Premium**\n"
        "Then provide the duration, payment method, and any extra details."
        if product == "Nitro"
        else "Tell us how many slots you need, the duration, and any setup details. Staff will confirm current availability and the final price."
        if product == "Slots"
        else "Please provide:\n1. Quantity / Duration\n2. Payment method\n3. Extra details"
    )
    embed = discord.Embed(
        title=f"Order — {product}",
        description=f"Welcome {user.mention}!\n\n**Product:** `{product}`\n\n{order_steps}",
        color=get_color(config, "accent"),
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text=config.get("footer", "Time AutoMM"))

    staff = guild.get_role(config["roles"].get("staff", 0))
    mention = staff.mention if staff else "@Staff"
    await ticket.send(content=f"{user.mention} | {mention}", embed=embed, view=CloseTicketView())

    for key in ["ticket_logs", "deal_logs"]:
        ch = guild.get_channel(config["channels"].get(key, 0))
        if ch:
            emb = discord.Embed(title=f"New Shop Order — {product}", color=get_color(config, "warning"), timestamp=datetime.utcnow())
            emb.add_field(name="User", value=f"{user.mention} (`{user.id}`)")
            emb.add_field(name="Ticket", value=ticket.mention)
            await ch.send(embed=emb)

    await interaction.response.send_message(f"✅ Order ticket created: {ticket.mention}", ephemeral=True)


class NitroView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        panels = load_panels()
        panel = panels.get("nitro", {})
        self.children[0].label = panel.get("button_label", "Buy Nitro")
        self.children[0].emoji = parse_button_emoji(panel.get("button_emoji", ""))

    @discord.ui.button(label="Buy Nitro", style=discord.ButtonStyle.primary, custom_id="buy_nitro")
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_shop_ticket(interaction, "Nitro")


class RobuxView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        panels = load_panels()
        panel = panels.get("robux", {})
        self.children[0].label = panel.get("button_label", "Buy Robux")
        self.children[0].emoji = parse_button_emoji(panel.get("button_emoji", ""))

    @discord.ui.button(label="Buy Robux", style=discord.ButtonStyle.success, custom_id="buy_robux")
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_shop_ticket(interaction, "Robux")


class SlotsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        panel = load_panels().get("slots", {})
        self.children[0].label = panel.get("button_label", "Order Slots")
        self.children[0].emoji = parse_button_emoji(panel.get("button_emoji", ""))

    @discord.ui.button(label="Order Slots", style=discord.ButtonStyle.primary, custom_id="buy_slots")
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_shop_ticket(interaction, "Slots")


class BoostsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        panels = load_panels()
        panel = panels.get("boosts", {})
        self.children[0].label = panel.get("button_label", "Buy Server Boosts")
        self.children[0].emoji = parse_button_emoji(panel.get("button_emoji", ""))

    @discord.ui.button(label="Buy Server Boosts", style=discord.ButtonStyle.secondary, custom_id="buy_boosts")
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_shop_ticket(interaction, "Server Boosts")


class Shop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def make_embed(self, key):
        config = load_config()
        panels = load_panels()
        p = panels.get(key, {})
        embed = discord.Embed(
            # Discord can leave external custom emoji markup literal in embed titles.
            # For Boosts, render the formatted title as normal message content instead.
            title=None if key in {"boosts", "robux", "nitro", "slots"} else p.get("title", key.title()),
            description=p.get("description", ""),
            color=get_color(config, "main"),
            timestamp=datetime.utcnow()
        )
        if key == "boosts" and p.get("image_url"):
            embed.set_image(url=p["image_url"])
        if key == "robux":
            embed.description = (
                f"{p.get('description', '')}\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"**💸 PRICE**\n✦ `{p.get('price', '$4.50 per 1,000 Robux')}`\n\n"
                f"**📦 LIVE AVAILABILITY**\n✦ {p.get('stock', 'Ask for current stock')}\n\n"
                "**⚡ Fast delivery  •  🔒 Safe ordering**\n"
                "Tap **Order Robux** below and tell us the amount you need."
            )
        if key == "nitro":
            embed.description = (
                f"{p.get('description', '')}\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "**🌙 NITRO BASIC**\n"
                f"💸 `{p.get('basic_price', 'Ask for price')}`  •  📦 {p.get('basic_stock', 'Ask for stock')}\n\n"
                "**💎 NITRO PREMIUM**\n"
                f"💸 `{p.get('premium_price', 'Ask for price')}`  •  📦 {p.get('premium_stock', 'Ask for stock')}\n\n"
                "**🛡️ FULL WARRANTY**\n"
                "If Nitro is revoked, DM us or open a ticket within **24 hours** of the revocation.\n\n"
                "Tap **Order Nitro** below and choose Basic or Premium."
            )
        if key == "slots":
            embed.description = p.get("description", "")
        embed.set_footer(text=config.get("footer", "Time AutoMM"))
        return embed

    def remember_panel_message(self, key, message, banner_message=None):
        os.makedirs("data", exist_ok=True)
        try:
            with open(PANEL_MESSAGES_FILE, "r", encoding="utf-8") as file:
                references = json.load(file)
        except (OSError, json.JSONDecodeError):
            references = {}
        references.setdefault(key, {})[str(message.guild.id)] = {
            "channel_id": message.channel.id,
            "message_id": message.id,
            "banner_message_id": banner_message.id if banner_message else None,
        }
        with open(PANEL_MESSAGES_FILE, "w", encoding="utf-8") as file:
            json.dump(references, file, indent=2)

    async def refresh_saved_panel(self, key, guild):
        try:
            with open(PANEL_MESSAGES_FILE, "r", encoding="utf-8") as file:
                references = json.load(file)
        except (OSError, json.JSONDecodeError):
            return False
        reference = references.get(key, {}).get(str(guild.id))
        if not reference:
            return False
        channel = guild.get_channel(int(reference.get("channel_id", 0)))
        if channel is None:
            return False
        try:
            message = await channel.fetch_message(int(reference["message_id"]))
            if key in {"nitro", "robux", "slots"}:
                view = {"nitro": NitroView, "robux": RobuxView, "slots": SlotsView}[key]()
                await message.edit(content=self.make_panel_content(key), embed=self.make_embed(key), view=view)
                image_url = load_panels().get(key, {}).get("image_url", "")
                banner_id = reference.get("banner_message_id")
                if image_url and banner_id:
                    banner = await channel.fetch_message(int(banner_id))
                    await banner.edit(embed=self.make_banner_embed(key))
                elif image_url and not banner_id:
                    banner = await channel.send(embed=self.make_banner_embed(key))
                    replacement = await channel.send(
                        content=self.make_panel_content(key), embed=self.make_embed(key), view=view
                    )
                    await message.delete()
                    reference["message_id"] = replacement.id
                    reference["banner_message_id"] = banner.id
                    references[key][str(guild.id)] = reference
                    with open(PANEL_MESSAGES_FILE, "w", encoding="utf-8") as file:
                        json.dump(references, file, indent=2)
                elif not image_url and banner_id:
                    try:
                        banner = await channel.fetch_message(int(banner_id))
                        await banner.delete()
                    except discord.NotFound:
                        pass
                    reference["banner_message_id"] = None
                    references[key][str(guild.id)] = reference
                    with open(PANEL_MESSAGES_FILE, "w", encoding="utf-8") as file:
                        json.dump(references, file, indent=2)
            else:
                await message.edit(embed=self.make_embed(key))
            return True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, KeyError, ValueError):
            return False

    def make_banner_embed(self, key):
        panel = load_panels().get(key, {})
        embed = discord.Embed(color=get_color(load_config(), "main"))
        if panel.get("image_url"):
            embed.set_image(url=panel["image_url"])
        return embed

    def make_panel_content(self, key):
        if key not in {"boosts", "robux", "nitro", "slots"}:
            return None
        return load_panels().get(key, {}).get("title")

    @app_commands.command(name="sendnitropanel", description="Send Nitro panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def sendnitropanel(self, interaction: discord.Interaction):
        banner = None
        if load_panels().get("nitro", {}).get("image_url"):
            banner = await interaction.channel.send(embed=self.make_banner_embed("nitro"))
        message = await interaction.channel.send(content=self.make_panel_content("nitro"),
                                                 embed=self.make_embed("nitro"), view=NitroView())
        self.remember_panel_message("nitro", message, banner)
        await interaction.response.send_message("✅ Nitro panel sent!", ephemeral=True)

    @app_commands.command(name="sendrobuxpanel", description="Send Robux panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def sendrobuxpanel(self, interaction: discord.Interaction):
        banner = None
        if load_panels().get("robux", {}).get("image_url"):
            banner = await interaction.channel.send(embed=self.make_banner_embed("robux"))
        message = await interaction.channel.send(content=self.make_panel_content("robux"),
                                                 embed=self.make_embed("robux"), view=RobuxView())
        self.remember_panel_message("robux", message, banner)
        await interaction.response.send_message("✅ Robux panel sent!", ephemeral=True)

    @app_commands.command(name="sendslotspanel", description="Send Slots shop panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def sendslotspanel(self, interaction: discord.Interaction):
        banner = None
        if load_panels().get("slots", {}).get("image_url"):
            banner = await interaction.channel.send(embed=self.make_banner_embed("slots"))
        message = await interaction.channel.send(content=self.make_panel_content("slots"),
                                                 embed=self.make_embed("slots"), view=SlotsView())
        self.remember_panel_message("slots", message, banner)
        await interaction.response.send_message("✅ Slots panel sent!", ephemeral=True)

    @app_commands.command(name="sendboostspanel", description="Send Boosts panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def sendboostspanel(self, interaction: discord.Interaction):
        await interaction.channel.send(content=self.make_panel_content("boosts"),
                                       embed=self.make_embed("boosts"), view=BoostsView())
        await interaction.response.send_message("✅ Boosts panel sent!", ephemeral=True)

    @commands.command(name="sendnitropanel")
    @commands.has_permissions(administrator=True)
    async def sendnitropanel_p(self, ctx):
        banner = None
        if load_panels().get("nitro", {}).get("image_url"):
            banner = await ctx.send(embed=self.make_banner_embed("nitro"))
        message = await ctx.send(content=self.make_panel_content("nitro"),
                                 embed=self.make_embed("nitro"), view=NitroView())
        self.remember_panel_message("nitro", message, banner)

    @commands.command(name="sendrobuxpanel")
    @commands.has_permissions(administrator=True)
    async def sendrobuxpanel_p(self, ctx):
        banner = None
        if load_panels().get("robux", {}).get("image_url"):
            banner = await ctx.send(embed=self.make_banner_embed("robux"))
        message = await ctx.send(content=self.make_panel_content("robux"),
                                 embed=self.make_embed("robux"), view=RobuxView())
        self.remember_panel_message("robux", message, banner)

    @commands.command(name="sendslotspanel")
    @commands.has_permissions(administrator=True)
    async def sendslotspanel_p(self, ctx):
        banner = None
        if load_panels().get("slots", {}).get("image_url"):
            banner = await ctx.send(embed=self.make_banner_embed("slots"))
        message = await ctx.send(content=self.make_panel_content("slots"),
                                 embed=self.make_embed("slots"), view=SlotsView())
        self.remember_panel_message("slots", message, banner)

    @commands.command(name="sendboostspanel")
    @commands.has_permissions(administrator=True)
    async def sendboostspanel_p(self, ctx):
        await ctx.send(content=self.make_panel_content("boosts"),
                       embed=self.make_embed("boosts"), view=BoostsView())


async def setup(bot):
    await bot.add_cog(Shop(bot))
    bot.add_view(NitroView())
    bot.add_view(RobuxView())
    bot.add_view(SlotsView())
    bot.add_view(BoostsView())
