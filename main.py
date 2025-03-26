import discord
from discord.ext import commands
import sqlite3
import os
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
TOKEN = os.environ.get('DISCORD_TOKEN')
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
c.execute("""
CREATE TABLE IF NOT EXISTS vouch_records (
    voucher_id INTEGER,
    vouched_id INTEGER,
    PRIMARY KEY (voucher_id, vouched_id)
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS unvouchable_users (
    user_id INTEGER PRIMARY KEY
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

def has_vouched(voucher_id, vouched_id):
    c.execute("SELECT 1 FROM vouch_records WHERE voucher_id = ? AND vouched_id = ?", (voucher_id, vouched_id))
    return c.fetchone() is not None

def record_vouch(voucher_id, vouched_id):
    c.execute("INSERT INTO vouch_records (voucher_id, vouched_id) VALUES (?, ?)", (voucher_id, vouched_id))
    conn.commit()

def is_unvouchable(user_id):
    c.execute("SELECT 1 FROM unvouchable_users WHERE user_id = ?", (user_id,))
    return c.fetchone() is not None

def set_unvouchable(user_id, status=True):
    if status:
        c.execute("INSERT OR IGNORE INTO unvouchable_users (user_id) VALUES (?)", (user_id,))
    else:
        c.execute("DELETE FROM unvouchable_users WHERE user_id = ?", (user_id,))
    conn.commit()

async def update_nickname(member):
    if is_tracking_enabled(member.id):
        vouches = get_vouches(member.id)
        current_nick = member.nick or member.name
        
        # Remove any existing tags
        if "[" in current_nick and "]" in current_nick:
            current_nick = current_nick.split("[")[0].strip()
        
        # Add appropriate tags
        tags = []
        if vouches > 0:
            tags.append(f"{vouches}V")
        if is_unvouchable(member.id):
            tags.append("unvouchable")
        
        new_nick = current_nick
        if tags:
            new_nick = f"{current_nick} [{', '.join(tags)}]"
        
        try:
            await member.edit(nick=new_nick)
        except discord.Forbidden:
            await member.send("I don't have permission to change your nickname!")
        except Exception as e:
            await member.send(f"An error occurred while updating your nickname: {e}")

# Admin check function
def is_admin(ctx):
    admin_roles = ["Administrator™🌟", "𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌𝓃𝓮𝓇 𓂀✅"]
    return any(role.name in admin_roles for role in ctx.author.roles)

# Event handlers and commands...

@bot.command()
@commands.check(is_admin)
async def unvouchable(ctx, member: discord.Member, status: str = "on"):
    """[ADMIN] Makes a user unvouchable or removes unvouchable status."""
    try:
        if status.lower() in ["on", "enable", "true"]:
            set_unvouchable(member.id, True)
            await ctx.send(f"{member.mention} is now unvouchable!")
        else:
            set_unvouchable(member.id, False)
            await ctx.send(f"{member.mention} can now be vouched again!")
        await update_nickname(member)
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
async def checkunvouchable(ctx, member: discord.Member = None):
    """Check if a user is unvouchable."""
    try:
        if member is None:
            member = ctx.author
        
        if is_unvouchable(member.id):
            await ctx.send(f"{member.mention} is currently unvouchable.")
        else:
            await ctx.send(f"{member.mention} can be vouched.")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
@commands.check(is_admin)
async def unvouchable_list(ctx):
    """[ADMIN] Lists all unvouchable users."""
    try:
        c.execute("SELECT user_id FROM unvouchable_users")
        unvouchable_users = c.fetchall()
        
        if not unvouchable_users:
            await ctx.send("No users are currently unvouchable.")
            return
            
        user_list = []
        for user_id in unvouchable_users:
            member = ctx.guild.get_member(user_id[0])
            if member:
                user_list.append(f"{member.mention} ({member.name}#{member.discriminator})")
        
        chunk_size = 10
        chunks = [user_list[i:i + chunk_size] for i in range(0, len(user_list), chunk_size)]
        
        for i, chunk in enumerate(chunks):
            if i == 0:
                message = f"**Unvouchable users ({len(unvouchable_users)} total):**\n" + "\n".join(chunk)
            else:
                message = "\n".join(chunk)
            await ctx.send(message)
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
async def vouch(ctx, member: discord.Member):
    """Vouch for another user."""
    try:
        # Admin bypass for all restrictions
        admin_bypass = is_admin(ctx)
        
        if not admin_bypass:
            if ctx.channel.name != "✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔":
                await ctx.send("This command can only be used in #✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔.")
                return
        
        if ctx.author == member and not admin_bypass:
            await ctx.send("You cannot vouch for yourself!")
            return
            
        if has_vouched(ctx.author.id, member.id) and not admin_bypass:
            await ctx.send("You already vouched them once. You cannot vouch them again!")
            return
            
        if not is_tracking_enabled(member.id):
            await ctx.send(f"{member.mention} has not enabled vouch tracking!")
            return
            
        if is_unvouchable(member.id) and not admin_bypass:
            await ctx.send(f"{member.mention} is unvouchable!")
            return
            
        count = get_vouches(member.id) + 1
        set_vouches(member.id, count)
        if not admin_bypass:  # Admins don't get recorded in vouch records
            record_vouch(ctx.author.id, member.id)
        await update_nickname(member)
        
        log_channel = discord.utils.get(ctx.guild.channels, name="✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔")
        if log_channel:
            await log_channel.send(f"{member.mention} has been vouched by {ctx.author.mention}. New total: {count}")
        
        await ctx.send(f"{member.mention} now has {count} vouches!")
    except Exception as e:
        await ctx.send(f"An error occurred while processing the vouch: {e}")

@bot.command()
@commands.check(is_admin)
async def clearvouches(ctx, member: discord.Member):
    """[ADMIN] Clears a user's vouches and removes the [XV] tag."""
    try:
        set_vouches(member.id, 0)
        c.execute("DELETE FROM vouch_records WHERE vouched_id = ?", (member.id,))
        conn.commit()
        await update_nickname(member)
        await ctx.send(f"Cleared vouches for {member.mention}!")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
@commands.check(is_admin)
async def clearvouches_all(ctx):
    """[ADMIN] Clears all vouches for all users."""
    try:
        c.execute("UPDATE vouches SET vouch_count = 0")
        c.execute("DELETE FROM vouch_records")
        conn.commit()
        
        for member in ctx.guild.members:
            if is_tracking_enabled(member.id):
                await update_nickname(member)
        
        await ctx.send("Cleared all vouches for all users!")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

# ... (rest of your existing commands remain the same)

keep_alive()
bot.run(TOKEN)
