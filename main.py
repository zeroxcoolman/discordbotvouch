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
    raise ValueError("No Discord token found!")
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Database setup with error handling
def get_db():
    conn = sqlite3.connect("vouches.db", timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS vouches (
            user_id INTEGER PRIMARY KEY,
            vouch_count INTEGER DEFAULT 0,
            tracking_enabled INTEGER DEFAULT 0
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS vouch_records (
            voucher_id INTEGER,
            vouched_id INTEGER,
            PRIMARY KEY (voucher_id, vouched_id)
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS unvouchable_users (
            user_id INTEGER PRIMARY KEY
        )
        """)

init_db()

# Database operations with error handling
def db_execute(query, params=()):
    try:
        with get_db() as conn:
            conn.execute(query, params)
        return True
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return False

def db_fetchone(query, params=()):
    try:
        with get_db() as conn:
            return conn.execute(query, params).fetchone()
    except sqlite3.Error:
        return None

def db_fetchall(query, params=()):
    try:
        with get_db() as conn:
            return conn.execute(query, params).fetchall()
    except sqlite3.Error:
        return []

# Core functions
def is_admin(ctx):
    admin_roles = ["Administrator™🌟", "𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌𝓷𝓮𝓻 𓂀✅"]
    return any(role.name in admin_roles for role in ctx.author.roles)

def get_vouches(user_id):
    row = db_fetchone("SELECT vouch_count FROM vouches WHERE user_id = ?", (user_id,))
    return row[0] if row else 0

def is_tracking_enabled(user_id):
    row = db_fetchone("SELECT tracking_enabled FROM vouches WHERE user_id = ?", (user_id,))
    return row and row[0] == 1

def is_unvouchable(user_id):
    row = db_fetchone("SELECT 1 FROM unvouchable_users WHERE user_id = ?", (user_id,))
    return row is not None

def has_vouched(voucher_id, vouched_id):
    row = db_fetchone("SELECT 1 FROM vouch_records WHERE voucher_id = ? AND vouched_id = ?", (voucher_id, vouched_id))
    return row is not None

async def update_nickname(member):
    try:
        if not is_tracking_enabled(member.id):
            return
            
        vouches = get_vouches(member.id)
        current_nick = member.display_name
        
        # Remove existing tags
        if "[" in current_nick and "]" in current_nick:
            current_nick = current_nick.split("[")[0].strip()
        
        # Build new tags
        tags = []
        if vouches > 0:
            tags.append(f"{vouches}V")
        if is_unvouchable(member.id):
            tags.append("unvouchable")
        
        new_nick = f"{current_nick} [{', '.join(tags)}]" if tags else current_nick
        
        try:
            await member.edit(nick=new_nick[:32])  # Enforce Discord's 32-char limit
        except (discord.Forbidden, discord.HTTPException):
            pass  # Silently fail on permission issues
    except Exception as e:
        print(f"Nick update error: {e}")

# Event handlers
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("❌ Unknown command. Use `!help` for available commands.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument. Usage: `!{ctx.command.name} {ctx.command.signature}`")
    else:
        await ctx.send("❌ An error occurred. Please try again.")
        print(f"Command error: {error}")

# ========================
# CORE COMMANDS
# ========================

@bot.command()
@commands.check(is_admin)
async def unvouchable(ctx, member: discord.Member, action: str = "on"):
    """[ADMIN] Toggle unvouchable status (on/off)"""
    action = action.lower()
    if action in ("on", "enable", "yes", "true", "1"):
        if not db_execute("INSERT OR IGNORE INTO unvouchable_users VALUES (?)", (member.id,)):
            return await ctx.send("❌ Failed to update database!")
        await ctx.send(f"🔒 {member.mention} is now unvouchable!")
    else:
        if not db_execute("DELETE FROM unvouchable_users WHERE user_id = ?", (member.id,)):
            return await ctx.send("❌ Failed to update database!")
        await ctx.send(f"🔓 {member.mention} can now be vouched!")
    await update_nickname(member)

@bot.command()
async def checkunvouchable(ctx, member: discord.Member = None):
    """Check if a user is unvouchable"""
    target = member or ctx.author
    status = "🔒 UNVOUCHABLE" if is_unvouchable(target.id) else "🔓 Vouchable"
    await ctx.send(f"{target.mention}: {status}")

@bot.command()
@commands.check(is_admin)
async def unvouchable_list(ctx):
    """[ADMIN] List all unvouchable users"""
    unvouchables = db_fetchall("SELECT user_id FROM unvouchable_users")
    if not unvouchables:
        return await ctx.send("No unvouchable users!")
    
    members = [ctx.guild.get_member(row[0]) for row in unvouchables]
    members = [m for m in members if m]
    
    msg = "🔒 Unvouchable Users:\n" + "\n".join(f"{m.mention} ({m.display_name})" for m in members)
    await ctx.send(msg[:2000])  # Prevent message overflow

@bot.command()
async def vouch(ctx, member: discord.Member):
    """Vouch for a user"""
    try:
        admin = is_admin(ctx)
        
        # Validations (unless admin)
        if not admin:
            if ctx.channel.name != "✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔":
                return await ctx.send("❌ Use the vouch channel!")
            if ctx.author == member:
                return await ctx.send("❌ You can't vouch yourself!")
            if has_vouched(ctx.author.id, member.id):
                return await ctx.send("❌ You already vouched them!")
            if is_unvouchable(member.id):
                return await ctx.send("❌ This user is unvouchable!")
            if not is_tracking_enabled(member.id):
                return await ctx.send("❌ User hasn't enabled tracking!")

        # Process vouch
        new_count = get_vouches(member.id) + 1
        if not db_execute("""
        INSERT INTO vouches VALUES (?, ?, 1) 
        ON CONFLICT(user_id) DO UPDATE SET vouch_count = ?
        """, (member.id, new_count, new_count)):
            return await ctx.send("❌ Database error!")
        
        if not admin:
            if not db_execute("INSERT INTO vouch_records VALUES (?, ?)", (ctx.author.id, member.id)):
                return await ctx.send("❌ Database error!")
        
        await update_nickname(member)
        await ctx.send(f"✅ {member.mention} now has {new_count} vouches!")
        
    except Exception as e:
        await ctx.send("❌ Failed to process vouch. Please try again.")
        print(f"Vouch error: {e}")

@bot.command()
@commands.check(is_admin)
async def clearvouches(ctx, member: discord.Member):
    """[ADMIN] Reset a user's vouches"""
    if not db_execute("UPDATE vouches SET vouch_count = 0 WHERE user_id = ?", (member.id,)):
        return await ctx.send("❌ Database error!")
    if not db_execute("DELETE FROM vouch_records WHERE vouched_id = ?", (member.id,)):
        return await ctx.send("❌ Database error!")
    await update_nickname(member)
    await ctx.send(f"♻️ Cleared vouches for {member.mention}!")

@bot.command()
@commands.check(is_admin)
async def clearvouches_all(ctx):
    """[ADMIN] Reset ALL vouches"""
    if not db_execute("UPDATE vouches SET vouch_count = 0"):
        return await ctx.send("❌ Database error!")
    if not db_execute("DELETE FROM vouch_records"):
        return await ctx.send("❌ Database error!")
    
    for member in ctx.guild.members:
        if is_tracking_enabled(member.id):
            await update_nickname(member)
    
    await ctx.send("♻️ Cleared ALL vouches!")

@bot.command()
@commands.check(is_admin)
async def setvouches(ctx, member: discord.Member, count: int):
    """[ADMIN] Set exact vouch count"""
    if count < 0:
        return await ctx.send("❌ Vouch count cannot be negative!")
    
    if not db_execute("""
    INSERT INTO vouches VALUES (?, ?, 1) 
    ON CONFLICT(user_id) DO UPDATE SET vouch_count = ?
    """, (member.id, count, count)):
        return await ctx.send("❌ Database error!")
    
    await update_nickname(member)
    await ctx.send(f"✅ Set {member.mention}'s vouches to {count}!")

@bot.command()
async def enablevouch(ctx):
    """Enable vouch tracking"""
    if not is_admin(ctx) and ctx.channel.name != "✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔":
        return await ctx.send("❌ Use the vouch channel!")
    
    if not db_execute("""
    INSERT INTO vouches (user_id, tracking_enabled) VALUES (?, 1) 
    ON CONFLICT(user_id) DO UPDATE SET tracking_enabled = 1
    """, (ctx.author.id,)):
        return await ctx.send("❌ Database error!")
    
    await update_nickname(ctx.author)
    await ctx.send(f"✅ Vouch tracking enabled for {ctx.author.mention}!")

@bot.command()
async def disablevouch(ctx):
    """Disable vouch tracking"""
    if not is_admin(ctx) and ctx.channel.name != "✅︱𝑽𝒐𝒖𝒄𝒉𝒆𝒔":
        return await ctx.send("❌ Use the vouch channel!")
    
    if not db_execute("UPDATE vouches SET tracking_enabled = 0 WHERE user_id = ?", (ctx.author.id,)):
        return await ctx.send("❌ Database error!")
    await update_nickname(ctx.author)
    await ctx.send(f"✅ Vouch tracking disabled for {ctx.author.mention}!")

@bot.command()
@commands.check(is_admin)
async def enablevouches_all(ctx):
    """[ADMIN] Enable tracking for all"""
    count = 0
    for member in ctx.guild.members:
        if not is_admin(ctx) and not is_tracking_enabled(member.id):
            if db_execute("""
            INSERT INTO vouches (user_id, tracking_enabled) VALUES (?, 1)
            ON CONFLICT(user_id) DO UPDATE SET tracking_enabled = 1
            """, (member.id,)):
                count += 1
                await update_nickname(member)
    
    await ctx.send(f"✅ Enabled tracking for {count} users!")

@bot.command()
@commands.check(is_admin)
async def disablevouches_all(ctx):
    """[ADMIN] Disable tracking for all"""
    count = 0
    for member in ctx.guild.members:
        if is_tracking_enabled(member.id):
            if db_execute("UPDATE vouches SET tracking_enabled = 0 WHERE user_id = ?", (member.id,)):
                count += 1
                await update_nickname(member)
    
    await ctx.send(f"✅ Disabled tracking for {count} users!")

@bot.command()
async def vouchstats(ctx, display: str = "count"):
    """View vouch statistics"""
    enabled_users = db_fetchall("SELECT user_id FROM vouches WHERE tracking_enabled = 1")
    count = len(enabled_users)
    
    if display.lower() == "list":
        if not is_admin(ctx):
            return await ctx.send("❌ Only admins can view the full list!")
        
        users = []
        for row in enabled_users:
            if member := ctx.guild.get_member(row[0]):
                users.append(f"{member.mention} ({member.display_name})")
        
        msg = f"📊 Users with tracking ({count}):\n" + "\n".join(users)
        await ctx.send(msg[:2000])
    else:
        await ctx.send(f"📊 {count} users have vouch tracking enabled")

@bot.command()
async def listvouches(ctx, member: discord.Member):
    """List all users who have vouched for this member"""
    vouchers = db_fetchall("""
    SELECT voucher_id 
    FROM vouch_records 
    WHERE vouched_id = ?
    """, (member.id,))
    
    if not vouchers:
        return await ctx.send(f"❌ No one has vouched for {member.mention} yet!")
    
    # Convert IDs to mentions
    mentions = []
    for row in vouchers:
        if voucher := ctx.guild.get_member(row[0]):
            mentions.append(voucher.mention)
    
    if not mentions:
        return await ctx.send(f"❌ Vouchers found but couldn't resolve members!")
    
    message = f"✅ Users who vouched for {member.mention}:\n" + "\n".join(mentions)
    await ctx.send(message[:2000])  # Prevent message overflow

keep_alive()
bot.run(TOKEN)
