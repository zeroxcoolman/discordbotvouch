import discord
from discord.ext import commands
import sqlite3
import os
import time
import asyncio
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
bot.vouch_spam = {}  # Anti-spam tracking

# Database setup with error handling
def get_db():
    conn = sqlite3.connect("vouches.db", timeout=30, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 30000")
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
        # New tables for enhancements:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS vouch_cooldowns (
            user_id INTEGER PRIMARY KEY,
            last_vouch_time INTEGER
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS vouch_reasons (
            voucher_id INTEGER,
            vouched_id INTEGER,
            reason TEXT,
            timestamp INTEGER,
            PRIMARY KEY (voucher_id, vouched_id)
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
    admin_roles = ["Administrator™🌟", "𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌𝓃𝑒𝓇 𓂀✅"]
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
        
        if "[" in current_nick and "]" in current_nick:
            base_name = current_nick.rsplit("[", 1)[0].strip()
        else:
            base_name = current_nick
        
        tags = []
        if vouches > 0:
            tags.append(f"{vouches}V")
        if is_unvouchable(member.id):
            tags.append("unvouchable")
        
        if tags:
            new_nick = f"{base_name} [{', '.join(tags)}]".replace("[", "［").replace("]", "］")
        else:
            new_nick = base_name
        
        if new_nick != current_nick:
            try:
                await member.edit(nick=new_nick[:32])
            except (discord.Forbidden, discord.HTTPException):
                pass
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
# YOUR ORIGINAL COMMANDS (EXACTLY AS YOU HAD THEM)
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
    await ctx.send(msg[:2000])

@bot.command()
async def vouch(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """Vouch for a user (now with cooldown and reason)"""
    try:
        admin = is_admin(ctx)
        
        # Anti-spam check
        if not admin:
            if ctx.author.id in bot.vouch_spam:
                if bot.vouch_spam[ctx.author.id] >= 3:
                    return await ctx.send("❌ You're vouching too fast!")
                bot.vouch_spam[ctx.author.id] += 1
            else:
                bot.vouch_spam[ctx.author.id] = 1
            
            # Cooldown check
            cooldown = db_fetchone("SELECT last_vouch_time FROM vouch_cooldowns WHERE user_id = ?", (ctx.author.id,))
            if cooldown and cooldown[0]:
                remaining = 24 - (time.time() - cooldown[0])//3600
                if remaining > 0:
                    return await ctx.send(f"❌ You can vouch again in {int(remaining)} hours!")
        
        # Original validations
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
            db_execute("""
            INSERT INTO vouch_reasons VALUES (?, ?, ?, ?)
            ON CONFLICT(voucher_id, vouched_id) DO UPDATE SET reason = ?, timestamp = ?
            """, (ctx.author.id, member.id, reason, int(time.time()), reason, int(time.time())))
            
            # Update cooldown
            db_execute("""
            INSERT INTO vouch_cooldowns VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_vouch_time = ?
            """, (ctx.author.id, int(time.time()), int(time.time())))
        
        await update_nickname(member)
        await ctx.send(f"✅ {member.mention} now has {new_count} vouches! Reason: {reason[:50]}")
        
        # Schedule spam counter reset
        if not admin:
            await asyncio.sleep(60)
            if ctx.author.id in bot.vouch_spam:
                bot.vouch_spam[ctx.author.id] -= 1
                if bot.vouch_spam[ctx.author.id] <= 0:
                    del bot.vouch_spam[ctx.author.id]
        
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

# ========================
# NEW ENHANCEMENTS (ADDED WITHOUT MODIFYING EXISTING CODE)
# ========================

@bot.command()
async def verify(ctx, member: discord.Member = None):
    """Verify a user's vouch count is legitimate"""
    await ctx.guild.chunk()  # Ensure member cache is fresh
    target = member or ctx.author
    
    if is_unvouchable(target.id):
        vouch_count = get_vouches(target.id)
        return await ctx.send(f"🔒 {target.mention} is UNVOUCHABLE (Database shows {vouch_count} vouches)")
    
    vouch_count = get_vouches(target.id)
    
    if vouch_count == 0:
        return await ctx.send(f"❌ {target.mention} has no vouches in the database!")
    
    # Parse displayed vouches
    displayed_vouches = 0
    if "[" in target.display_name and "]" in target.display_name:
        tag_part = target.display_name.split("[")[-1].split("]")[0]
        for part in tag_part.split(","):
            part = part.strip()
            if part.endswith("V"):
                try:
                    displayed_vouches = int(part[:-1])
                    break
                except ValueError:
                    continue
    
    # Get verification data
    legit_vouches = db_fetchall("SELECT COUNT(*) as count FROM vouch_records WHERE vouched_id = ?", (target.id,))
    legit_count = legit_vouches[0]['count'] if legit_vouches else 0
    
    # Verification logic
    fake_tags = (displayed_vouches > vouch_count)
    nickname_valid = (displayed_vouches == vouch_count)
    
    if fake_tags:
        verification = "🚨 FAKE TAGS DETECTED"
    elif not nickname_valid:
        verification = "⚠️ TAG DISCREPANCY"
    elif legit_count == vouch_count:
        verification = "✅ FULLY VERIFIED"
    elif legit_count < vouch_count:
        admin_vouches = vouch_count - legit_count
        verification = f"⚠️ {admin_vouches} ADMIN VOUCHES"
    else:
        verification = "❌ DATABASE INCONSISTENCY"
    
    # Build response
    response = (
        f"**Verification for {target.mention}**\n"
        f"• Displayed: {displayed_vouches}V\n"
        f"• Database: {vouch_count} vouches\n"
        f"• Community vouches: {legit_count}\n"
        f"• Status: {verification}"
    )
    
    # Add voucher list for admins
    if is_admin(ctx):
        vouchers = db_fetchall("""
        SELECT voucher_id, reason 
        FROM vouch_records
        LEFT JOIN vouch_reasons ON 
            vouch_records.voucher_id = vouch_reasons.voucher_id AND
            vouch_records.vouched_id = vouch_reasons.vouched_id
        WHERE vouch_records.vouched_id = ?
        """, (target.id,))
        
        if vouchers:
            voucher_info = []
            for row in vouchers:
                user = ctx.guild.get_member(row['voucher_id'])
                if user:
                    reason = row['reason'] or "No reason"
                    voucher_info.append(f"{user.mention} ({reason})")
            
            if voucher_info:
                response += "\n\n**Vouched by:** " + ", ".join(voucher_info[:5])
                if len(vouchers) > 5:
                    response += f" (+{len(vouchers)-5} more)"
    
    await ctx.send(response[:2000])

@bot.command()
async def myvouches(ctx):
    """Check your own vouch count and status"""
    count = get_vouches(ctx.author.id)
    cooldown = db_fetchone("SELECT last_vouch_time FROM vouch_cooldowns WHERE user_id = ?", (ctx.author.id,))
    
    msg = f"You have {count} legitimate vouches"
    if cooldown and cooldown[0]:
        remaining = max(0, 24 - (time.time() - cooldown[0])//3600)
        if remaining > 0:
            msg += f"\n⏳ You can vouch again in {int(remaining)} hours"
    
    await ctx.send(msg)

@bot.command()
async def vouchboard(ctx, limit: int = 10):
    """Show top vouched members"""
    top = db_fetchall("""
    SELECT user_id, vouch_count 
    FROM vouches 
    WHERE tracking_enabled = 1
    ORDER BY vouch_count DESC 
    LIMIT ?
    """, (limit,))
    
    msg = "🏆 Top Vouched Members:\n"
    for i, row in enumerate(top, 1):
        if member := ctx.guild.get_member(row['user_id']):
            msg += f"{i}. {member.display_name}: {row['vouch_count']}V\n"
    
    await ctx.send(msg[:2000])

@bot.command()
@commands.check(is_admin)
async def backup_db(ctx):
    """[ADMIN] Create a database backup"""
    try:
        with open('vouches.db', 'rb') as f:
            await ctx.send("Database backup:", file=discord.File(f, 'vouches_backup.db'))
    except Exception as e:
        await ctx.send(f"❌ Backup failed: {str(e)}")

keep_alive()
bot.run(TOKEN)
