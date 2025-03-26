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
""")  # Added the missing closing parenthesis here
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
        await ctx.send("Unknown command! Use `!help` to see available commands.", ephemeral=True)
    else:
        await ctx.send(f"An error occurred: {error}", ephemeral=True)

# Admin check function
def is_admin(ctx):
    admin_roles = ["Administrator™🌟", "𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌𝓃𝓮𝓇 𓂀✅"]
    return any(role.name in admin_roles for role in ctx.author.roles)

@bot.command()
@commands.check(is_admin)
async def clearvouches(ctx, member: discord.Member):
    """[ADMIN] Clears a user's vouches and removes the [XV] tag."""
    try:
        set_vouches(member.id, 0)
        # Clear all vouch records for this user
        c.execute("DELETE FROM vouch_records WHERE vouched_id = ?", (member.id,))
        conn.commit()
        
        base_name = member.nick if member.nick else member.name
        if "[" in base_name and "]" in base_name:
            base_name = base_name.split("[")[0].strip()
        try:
            await member.edit(nick=base_name)
        except discord.Forbidden:
            await ctx.reply(f"Couldn't update {member.mention}'s nickname (missing permissions).", ephemeral=True)
        await ctx.reply(f"Cleared vouches for {member.mention}!", ephemeral=True)
    except Exception as e:
        await ctx.reply(f"An error occurred: {e}", ephemeral=True)

@bot.command()
@commands.check(is_admin)
async def clearvouches_all(ctx):
    """[ADMIN] Clears all vouches for all users."""
    try:
        # Reset all vouch counts
        c.execute("UPDATE vouches SET vouch_count = 0")
        # Clear all vouch records
        c.execute("DELETE FROM vouch_records")
        conn.commit()
        
        # Remove all [XV] tags from nicknames
        for member in ctx.guild.members:
            if is_tracking_enabled(member.id):
                base_name = member.nick if member.nick else member.name
                if "[" in base_name and "]" in base_name:
                    base_name = base_name.split("[")[0].strip()
                try:
                    await member.edit(nick=base_name)
                except discord.Forbidden:
                    pass  # Skip if we can't change the nickname
        
        await ctx.reply("Cleared all vouches for all users!", ephemeral=True)
    except Exception as e:
        await ctx.reply(f"An error occurred: {e}", ephemeral=True)

@bot.command()
@commands.check(is_admin)
async def setvouches(ctx, member: discord.Member, count: int):
    """[ADMIN] Manually sets a user's vouch count."""
    try:
        if count < 0:
            await ctx.reply("Vouch count cannot be negative!", ephemeral=True)
            return
        set_vouches(member.id, count)
        await update_nickname(member)
        await ctx.reply(f"Set {member.mention}'s vouches to {count}!", ephemeral=True)
    except Exception as e:
        await ctx.reply(f"An error occurred: {e}", ephemeral=True)

@bot.command()
async def enablevouch(ctx):
    """Enable vouch tracking for yourself."""
    try:
        if not is_admin(ctx):
            if ctx.channel.name != "✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔":
                await ctx.reply("This command can only be used in #✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔.", ephemeral=True)
                return
        enable_tracking(ctx.author.id)
        await ctx.reply(f"Vouch tracking enabled for {ctx.author.mention}!", ephemeral=True)
    except Exception as e:
        await ctx.reply(f"An error occurred while enabling vouch tracking: {e}", ephemeral=True)

@bot.command()
@commands.check(is_admin)
async def enablevouches_all(ctx):
    """[ADMIN] Enable vouch tracking for all members (except admins)."""
    try:
        admin_roles = ["Administrator™🌟", "𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌�𝓃𝓮𝓇 𓂀✅"]
        count = 0
        
        for member in ctx.guild.members:
            # Skip if member has any admin role
            if any(role.name in admin_roles for role in member.roles):
                continue
                
            if not is_tracking_enabled(member.id):
                enable_tracking(member.id)
                count += 1
        
        await ctx.reply(f"Enabled vouch tracking for {count} members!", ephemeral=True)
    except Exception as e:
        await ctx.reply(f"An error occurred: {e}", ephemeral=True)

@bot.command()
async def disablevouch(ctx):
    """Disable vouch tracking for yourself."""
    try:
        if not is_admin(ctx):
            if ctx.channel.name != "✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔":
                await ctx.reply("This command can only be used in #✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔.", ephemeral=True)
                return
        disable_tracking(ctx.author.id)
        await ctx.reply(f"Vouch tracking disabled for {ctx.author.mention}!", ephemeral=True)
    except Exception as e:
        await ctx.reply(f"An error occurred while disabling vouch tracking: {e}", ephemeral=True)

@bot.command()
@commands.check(is_admin)
async def disablevouches_all(ctx):
    """[ADMIN] Disable vouch tracking for all members."""
    try:
        count = 0
        for member in ctx.guild.members:
            if is_tracking_enabled(member.id):
                disable_tracking(member.id)
                count += 1
                
                # Remove [XV] from nickname
                base_name = member.nick if member.nick else member.name
                if "[" in base_name and "]" in base_name:
                    base_name = base_name.split("[")[0].strip()
                try:
                    await member.edit(nick=base_name)
                except discord.Forbidden:
                    pass  # Skip if we can't change the nickname
        
        await ctx.reply(f"Disabled vouch tracking for {count} members!", ephemeral=True)
    except Exception as e:
        await ctx.reply(f"An error occurred: {e}", ephemeral=True)

@bot.command()
async def vouch(ctx, member: discord.Member):
    """Vouch for another user."""
    try:
        if not is_admin(ctx):
            if ctx.channel.name != "✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔":
                await ctx.reply("This command can only be used in #✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔.", ephemeral=True)
                return
        
        if ctx.author == member:
            await ctx.reply("You cannot vouch for yourself!", ephemeral=True)
            return
            
        if has_vouched(ctx.author.id, member.id):
            await ctx.reply("You already vouched them once. You cannot vouch them again!", ephemeral=True)
            return
            
        if not is_tracking_enabled(member.id):
            await ctx.reply(f"{member.mention} has not enabled vouch tracking!", ephemeral=True)
            return
            
        count = get_vouches(member.id) + 1
        set_vouches(member.id, count)
        record_vouch(ctx.author.id, member.id)
        await update_nickname(member)
        
        log_channel = discord.utils.get(ctx.guild.channels, name="✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔")
        if log_channel:
            await log_channel.send(f"{member.mention} has been vouched by {ctx.author.mention}. New total: {count}")
        
        await ctx.reply(f"{member.mention} now has {count} vouches!", ephemeral=True)
    except Exception as e:
        await ctx.reply(f"An error occurred while processing the vouch: {e}", ephemeral=True)

keep_alive()
bot.run(TOKEN)
