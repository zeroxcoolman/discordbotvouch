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
TOKEN = os.environ['DISCORD_TOKEN']  # Get the token from environment variable
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

def is_tracking_enabled(user_id):
    c.execute("SELECT tracking_enabled FROM vouches WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    return result[0] == 1 if result else False

def enable_tracking(user_id):
    c.execute("INSERT INTO vouches (user_id, tracking_enabled) VALUES (?, 1) ON CONFLICT(user_id) DO UPDATE SET tracking_enabled = 1", (user_id,))
    conn.commit()

def disable_tracking(user_id):
    c.execute("UPDATE vouches SET tracking_enabled = 0 WHERE user_id = ?", (user_id,))
    conn.commit()

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
        except Exception as e:
            await member.send(f"An error occurred while updating your nickname: {e}")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Unknown command! Use `!help` to see available commands.")
    else:
        await ctx.send(f"An error occurred: {error}")

@bot.command()
async def enablevouch(ctx):
    try:
        if "Administrator™🌟" not in [role.name for role in ctx.author.roles] and "𝓞𝔀𝓷𝓮𝓻 👑" not in [role.name for role in ctx.author.roles] and "𓂀 𝒞𝑜-𝒪𝓌𝓃𝑒𝓇 𓂀✅" not in [role.name for role in ctx.author.roles]:
            if ctx.channel.name != "✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔":
                await ctx.send("This command can only be used in #✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔.")
                return
        enable_tracking(ctx.author.id)
        await ctx.send(f"Vouch tracking enabled for {ctx.author.mention}!")
    except Exception as e:
        await ctx.send(f"An error occurred while enabling vouch tracking: {e}")

@bot.command()
async def disablevouch(ctx):
    try:
        if "Administrator™🌟" not in [role.name for role in ctx.author.roles] and "𝓞𝔀𝓷𝓮𝓻 👑" not in [role.name for role in ctx.author.roles] and "𓂀 𝒞𝑜-𝒪𝓌𝓃𝑒𝓇 𓂀✅" not in [role.name for role in ctx.author.roles]:
            if ctx.channel.name != "✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔":
                await ctx.send("This command can only be used in #✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔.")
                return
        disable_tracking(ctx.author.id)
        await ctx.send(f"Vouch tracking disabled for {ctx.author.mention}!")
    except Exception as e:
        await ctx.send(f"An error occurred while disabling vouch tracking: {e}")

@bot.command()
async def vouch(ctx, member: discord.Member):
    try:
        if "Administrator™🌟" not in [role.name for role in ctx.author.roles] and "𝓞𝔀𝓷𝓮𝓻 👑" not in [role.name for role in ctx.author.roles] and "𓂀 𝒞𝑜-𝒪𝓌𝓃𝑒𝓇 𓂀✅" not in [role.name for role in ctx.author.roles]:
            if ctx.channel.name != "✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔":
                await ctx.send("This command can only be used in #✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔.")
                return
        if ctx.author == member:
            await ctx.send("You cannot vouch for yourself!")
            return
        if not is_tracking_enabled(member.id):
            await ctx.send(f"{member.mention} has not enabled vouch tracking!")
            return
        count = get_vouches(member.id) + 1
        set_vouches(member.id, count)
        await update_nickname(member)
        log_channel = discord.utils.get(ctx.guild.channels, name="✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔")
        if log_channel:
            await log_channel.send(f"{member.mention} has been vouched by {ctx.author.mention}. New total: {count}")
        await ctx.send(f"{member.mention} now has {count} vouches!")
    except Exception as e:
        await ctx.send(f"An error occurred while processing the vouch: {e}")

keep_alive()
bot.run(TOKEN)
