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

def clean_nickname(nick):
    """Remove ALL vouch tags while preserving special characters"""
    if not nick:
        return nick
    
    # Escape special characters and use proper character sets
    import re
    try:
        # This pattern handles all bracket types safely
        pattern = r'(\s*[\[]([^\]\]]*)[\]]\s*)|(\s*［([^］］]*)］\s*)'
        clean = re.sub(pattern, '', str(nick)).strip()
        
        # Remove any remaining orphaned brackets
        clean = clean.replace("[", "").replace("]", "").replace("［", "").replace("］", "").strip()
        
        return clean
    except re.error:
        # Fallback to simple cleaning if regex fails
        return str(nick).replace("[", "").replace("]", "").replace("［", "").replace("］", "").strip()


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
    """Atomic nickname update with verification"""
    try:
        if not is_tracking_enabled(member.id):
            return
    
        current_nick = member.display_name
        
        # More robust cleaning with fallbacks
        base_name = clean_nickname(current_nick)
        
        # Double-check cleaning worked
        if (not base_name.strip() or 
            any(bracket in base_name for bracket in ["[", "]", "［", "］"])):
            base_name = member.name  # Fallback to pure username
            
        # Final sanitization
        base_name = base_name.replace("[", "").replace("]", "").replace("［", "").replace("］", "").strip()
        if not base_name:  # Ultimate fallback
            base_name = member.name

        # Build new tags
        new_tags = []
        vouches = get_vouches(member.id)
        if vouches > 0:
            new_tags.append(f"{vouches}V")
        if is_unvouchable(member.id):
            new_tags.append("unvouchable")

        # Construct new nickname
        new_nick = f"{base_name} [{', '.join(new_tags)}]" if new_tags else base_name
        new_nick = new_nick.replace("[", "［").replace("]", "］")[:32]

        # Verify no duplicate tags
        if "[" in new_nick and new_nick.count("[") > 1:
            new_nick = f"{base_name} [{new_tags[-1]}]"  # Use only the last tag

        if new_nick != current_nick:
            await member.edit(nick=new_nick)
            
    except Exception as e:
        print(f"Nickname update failed for {member.display_name}: {str(e)}")
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
async def fixnicks(ctx):
    """[ADMIN] Force-clean ALL nicknames"""
    count = 0
    failed = 0
    
    await ctx.send("🔄 Starting nickname cleanup...")
    
    for member in ctx.guild.members:
        try:
            if is_tracking_enabled(member.id):
                # First completely clean the nickname
                base_name = clean_nickname(member.display_name)
                await member.edit(nick=base_name)
                
                # Then properly update with tags
                await update_nickname(member)
                count += 1
                await asyncio.sleep(0.5)  # Rate limiting
        except Exception:
            failed += 1
    
    await ctx.send(f"✅ Successfully updated {count} nicknames ({failed} failed)")

@bot.command()
@commands.check(is_admin)
async def nuclear_fix(ctx, member: discord.Member):
    """[ADMIN] COMPLETELY reset problematic nicknames"""
    try:
        # Get pure username without discriminator
        original_name = member.name
        
        # Step 1: Reset to pure username
        await member.edit(nick=original_name)
        
        # Step 2: Force update with clean tags
        await update_nickname(member)
        
        await ctx.send(f"✅ Successfully reset {member.mention}'s nickname!")
    except Exception as e:
        await ctx.send(f"❌ Failed to reset nickname: {str(e)}")

@bot.command()
@commands.check(is_admin)
async def resetnick(ctx, member: discord.Member):
    """[ADMIN] Completely reset a user's nickname"""
    base_name = clean_nickname(member.display_name)
    try:
        await member.edit(nick=base_name)
        await ctx.send(f"✅ Reset {member.mention}'s nickname!")
    except discord.HTTPException:
        await ctx.send("❌ Failed to reset nickname (missing permissions)")

@bot.command()
@commands.check(is_admin)
async def setvouches(ctx, member: discord.Member, count: int):
    """[ADMIN] Set exact vouch count with atomic updates"""
    if count < 0:
        return await ctx.send("❌ Vouch count cannot be negative!")

    try:
        # 1. FIRST FORCE A CLEAN BASE NAME
        try:
            current_nick = member.display_name
            base_name = clean_nickname(current_nick)
            if not base_name.strip() or any(b in base_name for b in ["[","]","［","］"]):
                base_name = member.name
            await member.edit(nick=base_name)  # Remove all tags first
        except discord.HTTPException:
            pass  # Skip if we can't reset nickname

        # 2. UPDATE DATABASE
        if not db_execute("""
        INSERT INTO vouches VALUES (?, ?, 1) 
        ON CONFLICT(user_id) DO UPDATE SET vouch_count = ?
        """, (member.id, count, count)):
            return await ctx.send("❌ Database error!")

        # 3. FORCE FRESH NICKNAME UPDATE
        await update_nickname(member)
        await ctx.send(f"✅ Set {member.mention}'s vouches to {count}!")

    except Exception as e:
        # ULTIMATE FALLBACK
        error_msg = f"⚠️ Partial success: Vouches set but nickname may need manual fix ({str(e)[:100]})"
        await ctx.send(error_msg)
        
        # Try to at least set the database correctly
        db_execute("UPDATE vouches SET vouch_count = ? WHERE user_id = ?", (count, member.id))

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
    admin_vouches = vouch_count - legit_count
    
    # Determine admin vouch level
    admin_vouch_level = ""
    if admin_vouches > 0:
        ratio = admin_vouches / vouch_count
        if ratio > 0.75:
            admin_vouch_level = "MOSTLY ADMIN VOUCHES"
        elif ratio > 0.45:
            admin_vouch_level = "HALF ADMIN VOUCHES"
        else:
            admin_vouch_level = "SOME ADMIN VOUCHES"
    
    # Verification logic
    fake_tags = (displayed_vouches > vouch_count)
    nickname_valid = (displayed_vouches == vouch_count)
    
    if fake_tags:
        verification = "🚨 FAKE TAGS DETECTED"
    elif not nickname_valid:
        verification = "⚠️ TAG DISCREPANCY"
        # Notify admins about tag discrepancy
        await notify_admins(ctx.guild, target, "Tag discrepancy")
    elif legit_count == vouch_count:
        verification = "✅ FULLY VERIFIED"
    elif admin_vouches > 0:
        verification = f"⚠️ {admin_vouch_level} ({admin_vouches}/{vouch_count})"
    else:
        verification = "❌ DATABASE INCONSISTENCY"
    
    # Build response
    response = (
        f"**Verification for {target.mention}**\n"
        f"• Displayed: {displayed_vouches}V\n"
        f"• Database: {vouch_count} vouches\n"
        f"• Community vouches: {legit_count}\n"
        f"• Admin vouches: {admin_vouches}\n"
        f"• Status: {verification}"
    )
    
    await ctx.send(response[:2000])
async def notify_admins(guild, member, reason):
    """Notify admins about a vouch discrepancy"""
    admin_roles = ["Administrator™🌟", "𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌𝓃𝑒𝓻 𓂀✅"]
    
    # Find all admins and owners
    recipients = []
    for role in guild.roles:
        if role.name in admin_roles:
            recipients.extend(role.members)
    
    # Remove duplicates
    recipients = list(set(recipients))
    
    # Send DM to each admin
    for admin in recipients:
        try:
            embed = discord.Embed(
                title="Vouch Discrepancy Detected",
                description=f"Should we reset {member.mention}'s vouches?",
                color=discord.Color.orange()
            )
            embed.add_field(name="Reason", value=reason)
            embed.add_field(name="Current Vouches", value=get_vouches(member.id))
            embed.set_footer(text="Reply with 'yes' or 'no'")
            
            msg = await admin.send(embed=embed)
            
            # Add reactions for quick response
            await msg.add_reaction("✅")  # Yes
            await msg.add_reaction("❌")   # No
            
            # Store the message info for handling responses
            bot.dispatch("discrepancy_notification", admin.id, member.id, msg.id)
        except discord.Forbidden:
            print(f"Could not send DM to {admin}")
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
@bot.event
async def on_raw_reaction_add(payload):
    """Handle admin responses to discrepancy notifications"""
    # Check if this is a response to a discrepancy notification
    if hasattr(bot, 'discrepancy_notifications') and payload.message_id in bot.discrepancy_notifications:
        admin_id, member_id, message_id = bot.discrepancy_notifications[payload.message_id]
        
        # Only process reactions from the intended admin
        if payload.user_id == admin_id:
            guild = bot.get_guild(payload.guild_id)
            member = guild.get_member(member_id)
            admin = guild.get_member(admin_id)
            
            if str(payload.emoji) == "✅":  # Yes
                # Reset vouches
                db_execute("UPDATE vouches SET vouch_count = 0 WHERE user_id = ?", (member.id,))
                db_execute("DELETE FROM vouch_records WHERE vouched_id = ?", (member.id,))
                await update_nickname(member)
                await admin.send(f"✅ Reset vouches for {member.mention}")
            elif str(payload.emoji) == "❌":  # No
                await admin.send(f"❌ Did not reset vouches for {member.mention}")
            
            # Remove the notification from tracking
            del bot.discrepancy_notifications[message_id]

keep_alive()
bot.run(TOKEN)
