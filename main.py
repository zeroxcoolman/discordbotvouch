import discord
from discord.ext import commands
import sqlite3
import os  # For fetching the bot token from environment variables
from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run():
    PORT = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=PORT)

def keep_alive():
    t = Thread(target=run)
    t.start()

# Setup bot
TOKEN = os.environ.get('DISCORD_TOKEN')  # Get the token from environment variable
if TOKEN is None:
    raise ValueError("There is no discord token")
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Database setup
conn = sqlite3.connect("vouches.db")
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS vouches (
    user_id INTEGER PRIMARY KEY,
    vouch_count INTEGER DEFAULT 0,
    tracking_enabled INTEGER DEFAULT 0
)
""")
conn.commit()

def get_vouches(user_id):
    c.execute("SELECT vouch_count FROM vouches WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    return result[0] if result else 0

def set_vouches(user_id, count):
    c.execute("INSERT INTO vouches (user_id, vouch_count, tracking_enabled) VALUES (?, ?, 1) ON CONFLICT(user_id) DO UPDATE SET vouch_count = ?", (user_id, count, count))
    conn.commit()

def clear_vouches(user_id):
    c.execute("UPDATE vouches SET vouch_count = 0 WHERE user_id = ?", (user_id,))
    conn.commit()

def is_tracking_enabled(user_id):
    c.execute("SELECT tracking_enabled FROM vouches WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    return result[0] == 1 if result else False

async def update_nickname(member):
    if is_tracking_enabled(member.id):
        vouches = get_vouches(member.id)
        base_name = member.nick if member.nick else member.name
        if "[" in base_name and "]" in base_name:
            base_name = base_name.split("[")[0].strip()
        new_nick = f"{base_name} [{vouches}V]"
        try:
            await member.edit(nick=new_nick)
        except discord.Forbidden:
            await member.send("I don't have permission to change your nickname!")

@bot.command()
async def clearvouches(ctx, member: discord.Member):
    if any(role.name in ["Administrator™🌟", "𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌𝓃𝑒𝓇 𓂀✅"] for role in ctx.author.roles):
        clear_vouches(member.id)
        await update_nickname(member)
        await ctx.send(f"{member.mention}'s vouches have been cleared.")
    else:
        await ctx.send("You don't have permission to use this command!")

@bot.command()
async def setvouches(ctx, member: discord.Member, count: int):
    if any(role.name in ["Administrator™🌟", "𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌𝓃𝑒𝓻 𓂀✅"] for role in ctx.author.roles):
        set_vouches(member.id, count)
        await update_nickname(member)
        await ctx.send(f"{member.mention}'s vouches have been set to {count}.")
    else:
        await ctx.send("You don't have permission to use this command!")

keep_alive()
bot.run(TOKEN)
