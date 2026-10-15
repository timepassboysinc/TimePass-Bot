import discord
from discord.ext import commands
from discord import app_commands
import json
from utils.embeds import success_embed, error_embed, get_color

def load_config():
    with open("config.json", "r") as f:
        return json.load(f)

def save_config(config):
    with open("config.json", "w") as f:
        json.dump(config, f, indent=2)

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="blacklist", description="Blacklist a user from using MM services")
    @app_commands.describe(user="User to blacklist", reason="Reason for blacklist")
    @app_commands.checks.has_permissions(administrator=True)
    async def blacklist(self, interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
        config = load_config()
        role = interaction.guild.get_role(config["roles"].get("blacklisted", 0))

        if not role:
            return await interaction.response.send_message("❌ Blacklisted role is not configured in config.json", ephemeral=True)

        if role in user.roles:
            return await interaction.response.send_message("❌ User is already blacklisted.", ephemeral=True)

        await user.add_roles(role, reason=f"Blacklisted by {interaction.user}: {reason}")

        embed = success_embed(config, "User Blacklisted", f"{user.mention} has been blacklisted.\n**Reason:** {reason}")
        await interaction.response.send_message(embed=embed)

        # Log
        log_channel = interaction.guild.get_channel(config["channels"].get("blacklist", 0))
        if log_channel:
            log_embed = discord.Embed(
                title="User Blacklisted",
                color=get_color(config, "error"),
                timestamp=discord.utils.utcnow()
            )
            log_embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=False)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await log_channel.send(embed=log_embed)

    @app_commands.command(name="unblacklist", description="Remove a user from the blacklist")
    @app_commands.describe(user="User to unblacklist")
    @app_commands.checks.has_permissions(administrator=True)
    async def unblacklist(self, interaction: discord.Interaction, user: discord.Member):
        config = load_config()
        role = interaction.guild.get_role(config["roles"].get("blacklisted", 0))

        if not role:
            return await interaction.response.send_message("❌ Blacklisted role is not configured.", ephemeral=True)

        if role not in user.roles:
            return await interaction.response.send_message("❌ User is not blacklisted.", ephemeral=True)

        await user.remove_roles(role, reason=f"Unblacklisted by {interaction.user}")

        embed = success_embed(config, "User Unblacklisted", f"{user.mention} has been removed from the blacklist.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="setup", description="Quick help for setting up channel & role IDs")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Time AutoMM Setup Guide",
            description=(
                "1. Edit `config.json` and put your bot token + all channel/role/category IDs.\n"
                "2. Create the roles and channels as described in the full guide.\n"
                "3. Run `/sendpanel` in the panel channel.\n\n"
                "**Required IDs in config.json:**\n"
                "• `guild_id`\n"
                "• `channels.panel`\n"
                "• `channels.deal_logs`\n"
                "• `channels.blacklist`\n"
                "• `categories.auto_tickets`\n"
                "• `categories.human_tickets`\n"
                "• `roles.admin`\n"
                "• `roles.staff`\n"
                "• `roles.blacklisted`"
            ),
            color=0x4a6cf7
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="calcfee", description="Calculate the fee for a deal amount")
    @app_commands.describe(amount="Deal amount in USD")
    async def calcfee(self, interaction: discord.Interaction, amount: float):
        config = load_config()
        fees = config.get("fees", {})

        if amount < 50:
            fee = fees.get("under_50", 0.0)
            tier = "Under $50 (FREE)"
        elif amount < 250:
            fee = fees.get("under_250", 0.50)
            tier = "Under $250"
        else:
            fee = fees.get("over_250", 1.50)
            tier = "$250+"

        embed = discord.Embed(
            title="Fee Calculator",
            color=get_color(config, "accent")
        )
        embed.add_field(name="Deal Amount", value=f"${amount:.2f}", inline=True)
        embed.add_field(name="Tier", value=tier, inline=True)
        embed.add_field(name="Fee", value=f"**${fee:.2f}**", inline=True)
        embed.set_footer(text=config.get("footer", "Time AutoMM"))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Admin(bot))
