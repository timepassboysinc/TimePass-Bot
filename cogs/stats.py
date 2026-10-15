import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from datetime import datetime
from utils.embeds import get_color

DATA_FILE = "data/users.json"

def load_config():
    with open("config.json", "r") as f:
        return json.load(f)

def ensure_data():
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w") as f:
            json.dump({}, f)

def load_users():
    ensure_data()
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_users(data):
    ensure_data()
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_completed_deal(deal):
    """Count a completed deal once for each participant, regardless of display privacy."""
    users = load_users()
    deal_id = f"{deal['channel_id']}:{deal['id']}"
    amount = float(deal.get("usd") or 0)
    totals = {}
    for uid in {str(deal.get("sender_id")), str(deal.get("receiver_id"))} - {"None"}:
        data = users.setdefault(uid, {"deals": 0, "total_usd": 0.0, "biggest_deal": 0.0})
        recorded = data.setdefault("completed_deal_ids", [])
        if deal_id not in recorded:
            data["deals"] = int(data.get("deals", 0)) + 1
            data["total_usd"] = float(data.get("total_usd", 0)) + amount
            data["biggest_deal"] = max(float(data.get("biggest_deal", 0)), amount)
            recorded.append(deal_id)
        totals[uid] = float(data.get("total_usd", 0))
    save_users(users)
    return totals


async def sync_rank_roles(guild, totals):
    """Assign the existing stats rank roles by exact name when they are configured in Discord."""
    rank_names = [f"MM {name}" for _, name, _ in RANKS]
    for uid, total in totals.items():
        member = guild.get_member(int(uid))
        if member is None:
            continue
        current_name = get_rank(total)[0][1]
        current_role = discord.utils.get(guild.roles, name=f"MM {current_name}")
        if current_role is None:
            continue
        old_roles = [role for role in member.roles if role.name in rank_names and role != current_role]
        try:
            if old_roles:
                await member.remove_roles(*old_roles, reason="Auto MM stats rank update")
            if current_role and current_role not in member.roles:
                await member.add_roles(current_role, reason="Auto MM stats rank update")
        except discord.Forbidden:
            print("[stats] cannot update rank roles; move the bot role above the stats roles")
        except discord.HTTPException as exc:
            print(f"[stats] rank role update failed for member {uid}: {exc}")

# Rank thresholds (total USD volume)
RANKS = [
    (0,     "Member",   "⚪"),
    (100,   "Bronze",   "🥉"),
    (500,   "Quartz",   "⬜"),
    (1000,  "Amethyst", "💜"),
    (2500,  "Azure",    "💙"),
    (5000,  "Ruby",     "❤️"),
    (10000, "Emerald",  "💚"),
    (25000, "Diamond",  "💎"),
    (50000, "Obsidian", "🖤"),
]

def get_rank(total_usd: float):
    current = RANKS[0]
    next_rank = None
    for i, (threshold, name, emoji) in enumerate(RANKS):
        if total_usd >= threshold:
            current = (threshold, name, emoji)
            if i + 1 < len(RANKS):
                next_rank = RANKS[i + 1]
            else:
                next_rank = None
        else:
            break
    return current, next_rank


class Stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="stats", description="View your Auto MM stats and rank")
    @app_commands.describe(user="User to check (optional)")
    async def stats(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        users = load_users()
        uid = str(target.id)

        data = users.get(uid, {
            "deals": 0,
            "total_usd": 0.0,
            "biggest_deal": 0.0
        })

        deals = data.get("deals", 0)
        total = data.get("total_usd", 0.0)
        biggest = data.get("biggest_deal", 0.0)
        await sync_rank_roles(interaction.guild, {uid: float(total)})

        current, next_rank = get_rank(total)
        _, rank_name, rank_emoji = current

        embed = discord.Embed(
            title=f"{rank_emoji} {target.display_name}",
            color=get_color(load_config(), "accent"),
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        embed.add_field(name="Current Rank", value=f"{rank_emoji} **{rank_name}**", inline=True)

        if next_rank:
            next_threshold, next_name, next_emoji = next_rank
            embed.add_field(name="Next Rank", value=f"{next_emoji} {next_name} (${next_threshold:,})", inline=True)
        else:
            embed.add_field(name="Next Rank", value="Max Rank Achieved 🎉", inline=True)

        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="Deals Completed", value=f"**{deals}**", inline=True)
        embed.add_field(name="Total USD Value", value=f"**${total:,.2f}**", inline=True)
        embed.add_field(name="Biggest Deal", value=f"**${biggest:,.2f}**", inline=True)

        embed.set_footer(text=load_config().get("footer", "Time AutoMM"))
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="adddeal", description="(Staff) Add a completed deal to a user")
    @app_commands.describe(user="User", amount="Deal amount in USD")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def adddeal(self, interaction: discord.Interaction, user: discord.Member, amount: float):
        users = load_users()
        uid = str(user.id)

        if uid not in users:
            users[uid] = {"deals": 0, "total_usd": 0.0, "biggest_deal": 0.0}

        users[uid]["deals"] += 1
        users[uid]["total_usd"] += amount
        if amount > users[uid]["biggest_deal"]:
            users[uid]["biggest_deal"] = amount

        save_users(users)
        await sync_rank_roles(interaction.guild, {uid: float(users[uid]["total_usd"])})

        # Also log to completed-crypto if channel exists
        config = load_config()
        completed = interaction.guild.get_channel(config["channels"].get("completed_crypto", 0))
        if completed:
            log = discord.Embed(
                title="• Trade Completed",
                color=get_color(config, "success"),
                timestamp=datetime.utcnow()
            )
            log.add_field(name="Amount", value=f"${amount:.2f} USD", inline=False)
            log.add_field(name="User", value=user.mention, inline=True)
            log.add_field(name="Added by", value=interaction.user.mention, inline=True)
            await completed.send(embed=log)

        await interaction.response.send_message(
            f"✅ Added **${amount:.2f}** deal to {user.mention}. Total deals: {users[uid]['deals']}",
            ephemeral=True
        )

    @app_commands.command(name="leaderboard", description="Top users by volume")
    async def leaderboard(self, interaction: discord.Interaction):
        users = load_users()
        if not users:
            return await interaction.response.send_message("No deals recorded yet.", ephemeral=True)

        sorted_users = sorted(users.items(), key=lambda x: x[1].get("total_usd", 0), reverse=True)[:10]

        description = ""
        for i, (uid, data) in enumerate(sorted_users, 1):
            member = interaction.guild.get_member(int(uid))
            name = member.display_name if member else f"User {uid}"
            total = data.get("total_usd", 0)
            deals = data.get("deals", 0)
            rank = get_rank(total)[0]
            description += f"**{i}.** {rank[2]} {name} — `${total:,.2f}` ({deals} deals)\n"

        embed = discord.Embed(
            title="🏆 Leaderboard — Top Volume",
            description=description,
            color=get_color(load_config(), "accent"),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=load_config().get("footer", "Time AutoMM"))
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Stats(bot))
