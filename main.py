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

async def update_nickname(member):
    if is_tracking_enabled(member.id):
        vouches = get_vouches(member.id)
        current_nick = member.nick or member.name
        
        # Remove any existing [XV] tag
        if "[" in current_nick and "]" in current_nick:
            current_nick = current_nick.split("[")[0].strip()
        
        # Only add [XV] if vouches > 0
        new_nick = f"{current_nick} [{vouches}V]" if vouches > 0 else current_nick
        
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

# Admin check function
def is_admin(ctx):
    admin_roles = ["Administrator™🌟", "𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌𝓃𝓮𝓇 𓂀✅"]
    return any(role.name in admin_roles for role in ctx.author.roles)

@bot.command()
async def vouchstats(ctx, display: str = "count"):
    """
    Shows vouch tracking statistics.
    Usage: !vouchstats [count/list]
    - count: Shows just the number of users (default, available to everyone)
    - list: Shows the full list of users with tracking enabled (admin only)
    """
    try:
        c.execute("SELECT user_id FROM vouches WHERE tracking_enabled = 1")
        enabled_users = c.fetchall()
        
        if not enabled_users:
            await ctx.send("No users have vouch tracking enabled currently.")
            return
            
        count = len(enabled_users)
        
        if display.lower() == "list":
            if not is_admin(ctx):
                await ctx.send("Only admins can view the full list of users with vouch tracking enabled.")
                return
                
            user_list = []
            for user_id in enabled_users:
                member = ctx.guild.get_member(user_id[0])
                if member:
                    user_list.append(f"{member.mention} ({member.name}#{member.discriminator})")
            
            chunk_size = 10
            chunks = [user_list[i:i + chunk_size] for i in range(0, len(user_list), chunk_size)]
            
            for i, chunk in enumerate(chunks):
                if i == 0:
                    message = f"**Users with vouch tracking enabled ({count} total):**\n" + "\n".join(chunk)
                else:
                    message = "\n".join(chunk)
                await ctx.send(message)
        else:
            await ctx.send(f"**{count} users** have vouch tracking enabled.")
            
    except Exception as e:
        await ctx.send(f"An error occurred while fetching vouch stats: {e}")

@bot.command()
@commands.check(is_admin)
async def clearvouches(ctx, member: discord.Member):
    """[ADMIN] Clears a user's vouches and removes the [XV] tag."""
    try:
        set_vouches(member.id, 0)
        c.execute("DELETE FROM vouch_records WHERE vouched_id = ?", (member.id,))
        conn.commit()
        
        current_nick = member.nick or member.name
        if "[" in current_nick and "]" in current_nick:
            new_nick = current_nick.split("[")[0].strip()
            try:
                await member.edit(nick=new_nick)
            except discord.Forbidden:
                await ctx.send(f"Couldn't update {member.mention}'s nickname (missing permissions).")
        
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
                current_nick = member.nick or member.name
                if "[" in current_nick and "]" in current_nick:
                    new_nick = current_nick.split("[")[0].strip()
                    try:
                        await member.edit(nick=new_nick)
                    except discord.Forbidden:
                        pass
        
        await ctx.send("Cleared all vouches for all users!")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
@commands.check(is_admin)
async def setvouches(ctx, member: discord.Member, count: int):
    """[ADMIN] Manually sets a user's vouch count."""
    try:
        if count < 0:
            await ctx.send("Vouch count cannot be negative!")
            return
        set_vouches(member.id, count)
        await update_nickname(member)  # This will now handle the 0V case properly
        await ctx.send(f"Set {member.mention}'s vouches to {count}!")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
async def enablevouch(ctx):
    """Enable vouch tracking for yourself."""
    try:
        if not is_admin(ctx):
            if ctx.channel.name != "✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔":
                await ctx.send("This command can only be used in #✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔.")
                return
        enable_tracking(ctx.author.id)
        await update_nickname(ctx.author)
        await ctx.send(f"Vouch tracking enabled for {ctx.author.mention}!")
    except Exception as e:
        await ctx.send(f"An error occurred while enabling vouch tracking: {e}")

@bot.command()
@commands.check(is_admin)
async def enablevouches_all(ctx):
    """[ADMIN] Enable vouch tracking for all members (except admins)."""
    try:
        admin_roles = ["Administrator™🌟", "𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌�𝓃𝓮𝓇 𓂀✅"]
        count = 0
        
        for member in ctx.guild.members:
            if any(role.name in admin_roles for role in member.roles):
                continue
                
            if not is_tracking_enabled(member.id):
                enable_tracking(member.id)
                await update_nickname(member)
                count += 1
        
        await ctx.send(f"Enabled vouch tracking for {count} members!")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
async def disablevouch(ctx):
    """Disable vouch tracking for yourself."""
    try:
        if not is_admin(ctx):
            if ctx.channel.name != "✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔":
                await ctx.send("This command can only be used in #✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔.")
                return
        disable_tracking(ctx.author.id)
        current_nick = ctx.author.nick or ctx.author.name
        if "[" in current_nick and "]" in current_nick:
            new_nick = current_nick.split("[")[0].strip()
            try:
                await ctx.author.edit(nick=new_nick)
            except discord.Forbidden:
                pass
        await ctx.send(f"Vouch tracking disabled for {ctx.author.mention}!")
    except Exception as e:
        await ctx.send(f"An error occurred while disabling vouch tracking: {e}")

@bot.command()
@commands.check(is_admin)
async def disablevouches_all(ctx):
    """[ADMIN] Disable vouch tracking for all members."""
    try:
        count = 0
        for member in ctx.guild.members:
            if is_tracking_enabled(member.id):
                disable_tracking(member.id)
                current_nick = member.nick or member.name
                if "[" in current_nick and "]" in current_nick:
                    new_nick = current_nick.split("[")[0].strip()
                    try:
                        await member.edit(nick=new_nick)
                    except discord.Forbidden:
                        pass
                count += 1
        
        await ctx.send(f"Disabled vouch tracking for {count} members!")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
async def vouch(ctx, member: discord.Member):
    """Vouch for another user."""
    try:
        if not is_admin(ctx):
            if ctx.channel.name != "✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔":
                await ctx.send("This command can only be used in #✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔.")
                return
        
        if ctx.author == member:
            await ctx.send("You cannot vouch for yourself!")
            return
            
        if has_vouched(ctx.author.id, member.id):
            await ctx.send("You already vouched them once. You cannot vouch them again!")
            return
            
        if not is_tracking_enabled(member.id):
            await ctx.send(f"{member.mention} has not enabled vouch tracking!")
            return
            
        count = get_vouches(member.id) + 1
        set_vouches(member.id, count)
        record_vouch(ctx.author.id, member.id)
        await update_nickname(member)
        
        log_channel = discord.utils.get(ctx.guild.channels, name="✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔")
        if log_channel:
            await log_channel.send(f"{member.mention} has been vouched by {ctx.author.mention}. New total: {count}")
        
        await ctx.send(f"{member.mention} now has {count} vouches!")
    except Exception as e:
        await ctx.send(f"An error occurred while processing the vouch: {e}")

keep_alive()
bot.run(TOKEN)
