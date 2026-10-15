import discord
from discord.ext import commands
from utils.embeds import get_color
import json

def load_config():
    with open("config.json", "r") as f:
        return json.load(f)

class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help")
    @commands.has_permissions(administrator=True)
    async def help_command(self, ctx):
        config = load_config()
        embed = discord.Embed(title="Time AutoMM — Help", color=get_color(config, "accent"))

        embed.add_field(name="📌 Send Panels (Admin)", value=(
            "`.sendautopanel` — Auto MM (LTC/USDT/SOL)\n"
            "`.sendhumanpanel` — Human Middleman\n"
            "`.sendnitropanel` — Nitro\n"
            "`.sendrobuxpanel` — Robux\n"
            "`.sendslotspanel` — Slots\n"
            "`.sendboostspanel` — Server Boosts\n"
            "`.sendvouchpanel` — Reviews and vouches"
        ), inline=False)

        embed.add_field(name="✏️ Edit Panels (Admin)", value=(
            "`.editpanel <panel> <field> <new text>`\n\n"
            "**Examples:**\n"
            "`.editpanel auto title Time AutoMM`\n"
            "`.editpanel auto fees • Under $50 FREE`\n"
            "`.editpanel nitro button_label Buy Nitro Now`\n"
            "`.editpanel nitro button_emoji <:nitro:123456789012345678>`\n"
            "`.editpanel auto ltc_emoji <:ltc:123456789012345678>`\n"
            "`.editpanel human description New text here`\n"
            "`.editpanel robux price $4.50 per 1,000 Robux`\n"
            "`.editpanel robux stock 50,000 Robux available`\n"
            "`.editpanel nitro basic_price $X per month`\n"
            "`.editpanel nitro premium_price $Y per month`\n"
            "`.editpanel nitro basic_stock 10 available`\n"
            "`.editpanel nitro premium_stock 5 available`\n"
            "Use `none` as the emoji value to clear a button emoji. Supports auto crypto, human MM, Nitro, Robux, and boosts."
        ), inline=False)

        embed.add_field(name="🧪 Testing (Admin)", value="`.simpay` — dry-run only: fake the sender's payment in a deal ticket", inline=False)

        embed.add_field(name="📊 Stats", value=(
            "`/stats` `/stats @user`\n"
            "`/leaderboard`\n"
            "`/adddeal @user amount` (Staff)"
        ), inline=False)

        embed.add_field(name="🛡️ Admin", value=(
            "`/blacklist @user [reason]`\n"
            "`/unblacklist @user`\n"
            "`/calcfee amount`"
        ), inline=False)

        embed.set_footer(text=config.get("footer", "Time AutoMM"))
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Help(bot))
