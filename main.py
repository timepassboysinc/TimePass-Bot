import discord
from discord.ext import commands
import json
import os
import asyncio
from dotenv import load_dotenv
from keep_alive import keep_alive

load_dotenv()  # reads .env (WALLET_MNEMONIC, GAS_WALLET_KEY)

with open("config.json", "r") as f:
    config = json.load(f)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix=".", intents=intents, help_command=None)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Secure Middleman Service"
        )
    )

async def load_cogs():
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py") and not filename.startswith("__"):
            try:
                await bot.load_extension(f"cogs.{filename[:-3]}")
                print(f"✅ Loaded cog: {filename}")
            except Exception as e:
                print(f"❌ Failed to load cog {filename}: {e}")

async def main():
    token = os.getenv("DISCORD_TOKEN") or config.get("token")
    if not token:
        raise SystemExit("❌ No bot token. Set the DISCORD_TOKEN environment variable.")
    async with bot:
        await load_cogs()
        # If Discord/Cloudflare blocks this IP (429 / error 1015), wait instead of
        # crashing, so Render doesn't restart us every few seconds and extend the ban.
        while True:
            try:
                await bot.start(token)
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    print("⚠️ Discord/Cloudflare is blocking this IP (429). Waiting 30 minutes before retrying...")
                    await asyncio.sleep(1800)
                else:
                    raise

if __name__ == "__main__":
    keep_alive()  # tiny web server so Render + UptimeRobot keep the service awake
    asyncio.run(main())