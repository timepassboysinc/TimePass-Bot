import discord
from datetime import datetime

def get_color(config, key="main"):
    return int(config["colors"].get(key, "0x1a1a2e"), 16)

def main_embed(config, title: str, description: str = None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=get_color(config, "main"),
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text=config.get("footer", "Time AutoMM"))
    return embed

def success_embed(config, title: str, description: str = None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=get_color(config, "success"),
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text=config.get("footer", "Time AutoMM"))
    return embed

def error_embed(config, title: str, description: str = None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=get_color(config, "error"),
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text=config.get("footer", "Time AutoMM"))
    return embed

def warning_embed(config, title: str, description: str = None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=get_color(config, "warning"),
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text=config.get("footer", "Time AutoMM"))
    return embed

def panel_embed(config):
    """Exact style inspired by Jace's Auto Middleman panel"""
    embed = discord.Embed(
        title=f"**{config.get('bot_name', 'Time AutoMM')}**",
        color=get_color(config, "main"),
        timestamp=datetime.utcnow()
    )

    embed.add_field(
        name="\u200b",
        value=(
            "• **Paid Service**\n"
            f"• Read our ToS before using the bot: `{config.get('tos_channel', '#tos-crypto')}`"
        ),
        inline=False
    )

    fees = config.get("fees", {})
    embed.add_field(
        name="**Fees:**",
        value=(
            f"• Deals **$250+**: `${fees.get('over_250', 1.50):.2f}`\n"
            f"• Deals under **$250**: `${fees.get('under_250', 0.50):.2f}`\n"
            f"• Deals under **$50** are **FREE**"
        ),
        inline=False
    )

    embed.set_footer(text=config.get("footer", "Time AutoMM • Secure Middleman"))
    return embed

def deal_embed(config, crypto: str, deal_id: str, buyer: discord.Member = None, seller: discord.Member = None):
    embed = discord.Embed(
        title=f"Deal • {crypto.upper()}",
        color=get_color(config, "accent"),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Deal ID", value=f"`{deal_id}`", inline=True)
    embed.add_field(name="Crypto", value=crypto.upper(), inline=True)
    embed.add_field(name="Status", value="🟡 Waiting for details", inline=True)

    if buyer:
        embed.add_field(name="Buyer", value=buyer.mention, inline=True)
    if seller:
        embed.add_field(name="Seller", value=seller.mention, inline=True)

    embed.set_footer(text=config.get("footer", "Time AutoMM"))
    return embed
