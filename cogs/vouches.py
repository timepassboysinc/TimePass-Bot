import asyncio
import json
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import get_color


DATA_FILE = "data/vouches.json"
_write_lock = asyncio.Lock()


def load_config():
    with open("config.json", "r", encoding="utf-8") as file:
        return json.load(file)


def load_vouches():
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_vouches(data):
    os.makedirs("data", exist_ok=True)
    temporary = DATA_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(temporary, DATA_FILE)


class VouchRatingSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=f"{stars} Star{'s' if stars != 1 else ''}",
                                 value=str(stars),
                                 description=description,
                                 emoji="⭐")
            for stars, description in [
                (5, "Excellent experience"),
                (4, "Very good experience"),
                (3, "Average experience"),
                (2, "Disappointing experience"),
                (1, "Poor experience"),
            ]
        ]
        super().__init__(placeholder="Choose your score...", options=options,
                         min_values=1, max_values=1, custom_id="vouch:rating")

    async def callback(self, interaction: discord.Interaction):
        self.view.rating = int(self.values[0])
        await self.view.refresh(interaction)


class VouchLaneSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Order", value="Order", emoji="🛍️"),
            discord.SelectOption(label="Communication", value="Communication", emoji="💬"),
            discord.SelectOption(label="Delivery", value="Delivery", emoji="📦"),
            discord.SelectOption(label="Support", value="Support", emoji="🤝"),
            discord.SelectOption(label="Overall", value="Overall", emoji="✨"),
        ]
        super().__init__(placeholder="Pick the vibe...", options=options,
                         min_values=1, max_values=1, custom_id="vouch:lane")

    async def callback(self, interaction: discord.Interaction):
        self.view.lane = self.values[0]
        await self.view.refresh(interaction)


class VouchReviewModal(discord.ui.Modal, title="Craft Your Review"):
    review = discord.ui.TextInput(
        label="What should we remember?",
        placeholder="Example: The support was fast, clear, and made the whole order feel premium.",
        style=discord.TextStyle.paragraph,
        min_length=3,
        max_length=1000,
        required=True,
    )

    def __init__(self, author_id: int, guild_id: int, channel_id: int, rating: int, lane: str):
        super().__init__()
        self.author_id = author_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.rating = rating
        self.lane = lane

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id or interaction.guild_id != self.guild_id:
            return await interaction.response.send_message(
                "This review form belongs to another member.", ephemeral=True
            )

        guild = interaction.guild
        channel = guild.get_channel(self.channel_id) if guild else None
        if channel is None:
            return await interaction.response.send_message(
                "I can’t find the vouch channel. Ask staff to run `.sendvouchpanel` again there.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with _write_lock:
            data = load_vouches()
            guild_data = data.setdefault(str(self.guild_id), {"count": 0, "reviews": []})
            guild_data.setdefault("reviews", [])
            guild_data["count"] = int(guild_data.get("count", 0)) + 1
            number = guild_data["count"]
            published = datetime.now(timezone.utc)
            member = interaction.user
            review_text = str(self.review.value).strip()
            guild_data["reviews"].append({
                "number": number,
                "user_id": member.id,
                "rating": self.rating,
                "lane": self.lane,
                "review": review_text,
                "published_at": published.isoformat(),
            })
            save_vouches(data)

        config = load_config()
        stars = "⭐" * self.rating
        embed = discord.Embed(
            title=f"New {self.lane} Review",
            description=f"*“{review_text}”*",
            color=get_color(config, "accent"),
            timestamp=published,
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.add_field(name="Experience Score", value=f"{stars} **{self.rating} / 5**", inline=False)
        embed.add_field(name="Reviewed by", value=member.mention, inline=True)
        embed.add_field(name="Member ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Review Lane", value=self.lane, inline=True)
        embed.add_field(name="Vouch", value=f"**#{number}**", inline=True)
        embed.set_footer(text=f"Published • {config.get('footer', 'Time AutoMM')}")

        try:
            await channel.send(embed=embed, view=LeaveVouchView(), allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            return await interaction.followup.send(
                "The review was saved, but I can’t post in the vouch channel. Give the bot permission to send messages and embed links there.",
                ephemeral=True,
            )
        await interaction.followup.send("✅ Your review was published. Thanks for the vouch!", ephemeral=True)


class VouchFormView(discord.ui.View):
    def __init__(self, author_id: int, guild_id: int, channel_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.rating = None
        self.lane = None
        self.submit_component = next(
            item for item in self.children
            if isinstance(item, discord.ui.Button) and item.custom_id == "vouch:submit"
        )
        self.add_item(VouchRatingSelect())
        self.add_item(VouchLaneSelect())

    async def refresh(self, interaction: discord.Interaction):
        self.submit_component.disabled = self.rating is None or self.lane is None
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Submit Details", style=discord.ButtonStyle.primary,
                       custom_id="vouch:submit", disabled=True, row=2)
    async def submit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("This review form belongs to another member.", ephemeral=True)
        if self.rating is None or self.lane is None:
            return await interaction.response.send_message("Choose a score and a review lane first.", ephemeral=True)
        await interaction.response.send_modal(
            VouchReviewModal(self.author_id, self.guild_id, self.channel_id, self.rating, self.lane)
        )


class LeaveVouchView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Leave your Review", style=discord.ButtonStyle.primary,
                       custom_id="vouch:leave")
    async def leave_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or interaction.channel is None:
            return await interaction.response.send_message("Reviews can only be submitted in a server.", ephemeral=True)
        view = VouchFormView(interaction.user.id, interaction.guild.id, interaction.channel.id)
        await interaction.response.send_message(
            "Select a score and review lane, then submit your written review.",
            view=view,
            ephemeral=True,
        )


class Vouches(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def post_vouch_panel(self, channel):
        embed = discord.Embed(
            title="Leave a Review",
            description="Completed a deal with us? Share an honest review of your experience below.",
            color=get_color(load_config(), "accent"),
        )
        await channel.send(embed=embed, view=LeaveVouchView())

    @commands.command(name="sendvouchpanel")
    @commands.has_permissions(manage_guild=True)
    async def send_vouch_panel_prefix(self, ctx):
        await self.post_vouch_panel(ctx.channel)
        await ctx.send("✅ Vouch panel posted here.")

    @app_commands.command(name="sendvouchpanel", description="Post the review and vouch panel in this channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def send_vouch_panel_slash(self, interaction: discord.Interaction):
        await self.post_vouch_panel(interaction.channel)
        await interaction.response.send_message("✅ Vouch panel posted here.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Vouches(bot))
    bot.add_view(LeaveVouchView())
