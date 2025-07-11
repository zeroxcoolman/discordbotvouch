import re
import datetime
import traceback
import discord
from discord import ui
from discord.ext import commands
import sqlite3
import os
import time
import asyncio
from flask import Flask
from threading import Thread
from io import StringIO
import json

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
TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
if TOKEN is None:
    raise ValueError("No Discord token found!")
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot.vouch_spam = {}  # Anti-spam tracking
bot.discrepancy_notifications = {}
ADMIN_ALERTS_CHANNEL_ID = 1354897882271977744
# Admin channel configuration

def get_staff_channel(guild):
    staff_channel_id, _ = get_config(guild.id)
    return guild.get_channel(staff_channel_id)

def get_config(guild_id):
    row = db_fetchone("SELECT staff_channel_id, admin_roles_id FROM config WHERE guild_id = ?", (guild_id,))
    if row:
        return int(row['staff_channel_id']), json.loads(row['admin_roles_id'])
    return 0, []


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
            timestamp INTEGER DEFAULT 0,
            PRIMARY KEY (voucher_id, vouched_id)
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS unvouchable_users (
            user_id INTEGER PRIMARY KEY
        )
        """)
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
        # Add index for faster timestamp queries
        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_vouch_timestamp 
        ON vouch_records(timestamp)
        """)

init_db()

def init_config():
    with get_db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            guild_id INTEGER PRIMARY KEY,
            staff_channel_id TEXT DEFAULT '',
            admin_roles_id TEXT DEFAULT ''
        )
        """)


init_config()

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
    _, admin_roles_id = get_config(ctx.guild.id)
    return any(role.id in admin_roles_id for role in ctx.author.roles)

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

# Add this with your other utility functions (around line 100)
async def clean_old_notifications():
    """Clean up old notification records"""
    while True:
        await asyncio.sleep(3600)  # Every hour
        current_time = time.time()
        to_delete = []
        
        for msg_id, data in bot.discrepancy_notifications.items():
            if current_time - data.get('timestamp', 0) > 86400:  # 24 hours
                to_delete.append(msg_id)
        
        for msg_id in to_delete:
            del bot.discrepancy_notifications[msg_id]

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

class VouchModal(ui.Modal, title="Submit a Vouch"):
    person_name = ui.TextInput(label="Person Name", placeholder="Their Discord name or mention", required=True)
    reason = ui.TextInput(label="Reason", placeholder="Optional", required=False, style=discord.TextStyle.paragraph)

    def __init__(self, bot, interaction):
        super().__init__()
        self.bot = bot
        self.interaction = interaction

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Try to resolve the user by mention or name
            guild = interaction.guild
            target = None
            content = self.person_name.value.strip()

            # Try mention
            if len(interaction.message.mentions) > 0:
                target = interaction.message.mentions[0]
            else:
                # Try ID or name match
                match = re.match(r'<@!?(\d+)>', content)
                if match:
                    target_id = int(match.group(1))
                    target = guild.get_member(target_id)
                else:
                    # Try by name
                    for member in guild.members:
                        if member.name.lower() == content.lower():  # exact username match only
                            target = member
                            break

            if not target:
                await interaction.response.send_message(f"❌ Could not find user `{content}` in this server.", ephemeral=True)
                return

            # Call the existing !vouch command
            ctx = await self.bot.get_context(interaction.message)
            ctx.author = interaction.user
            ctx.guild = guild
            ctx.channel = interaction.channel
            
            old_send = ctx.send
            output_buffer = StringIO()
            
            async def capture_send(content=None, **kwargs):
                output_buffer.write(content or "")
            ctx.send = capture_send
            
            try:
                await self.bot.get_command('vouch').callback(ctx, target, reason=self.reason.value or "No reason provided")
                response_text = output_buffer.getvalue()
                if "✅" in response_text:
                    await interaction.response.send_message(f"✅ Vouch submitted for {target.mention}.", ephemeral=True)
                else:
                    await interaction.response.send_message(response_text, ephemeral=True)
            except Exception as e:
                await interaction.response.send_message("❌ Failed to process vouch.", ephemeral=True)
                print(f"VouchModal error: {e}")
            finally:
                ctx.send = old_send

class VouchButtonView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Submit A Vouch", style=discord.ButtonStyle.primary, emoji="🎟️",custom_id="submit_vouch_button")
    async def submit_vouch_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VouchModal(self.bot, interaction))

# COMMANDS

@bot.command()
@commands.is_owner()
async def setconfig(ctx, setting: str, *, value: str):
    """[OWNER] Set staff_channel_id or admin_roles_id (comma-separated IDs)"""
    setting = setting.lower()
    if setting not in ("staff_channel_id", "admin_roles_id"):
        return await ctx.send("❌ Invalid setting. Use `staff_channel_id` or `admin_roles_id`.")

    if setting == "staff_channel_id":
        try:
            channel_id = int(value.strip())
            channel = ctx.guild.get_channel(channel_id)
            if channel is None:
                return await ctx.send("❌ That channel ID doesn't exist in this server!")
            value = str(channel.id)
        except ValueError:
            return await ctx.send("❌ Invalid channel ID format. Use a numeric ID.")
    
    elif setting == "admin_roles_id":
        try:
            role_ids = [int(r.strip()) for r in value.split(",") if r.strip()]
            missing = [rid for rid in role_ids if ctx.guild.get_role(rid) is None]
            if missing:
                return await ctx.send(f"❌ These role IDs don't exist: {', '.join(map(str, missing))}")
            value = json.dumps(role_ids)
        except ValueError:
            return await ctx.send("❌ Invalid role ID format. Use numeric IDs separated by commas.")

    if not db_execute(f"""
        INSERT INTO config (guild_id, {setting})
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET {setting} = ?
    """, (ctx.guild.id, value, value)):
        return await ctx.send("❌ Failed to update config.")
    
    await ctx.send(f"✅ `{setting}` updated.")

@bot.command()
@commands.check(is_admin)
async def setupvouchticket(ctx):
    """[ADMIN] Set up the Submit A Vouch button in this channel."""
    view = VouchButtonView(bot)
    await ctx.send("📝 Click below to submit a vouch!", view=view)
    await ctx.send("✅ Vouch ticket system is ready.")


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
    """Vouch for a user (now with cooldown, reason, and DM notification)"""
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

        # ============================================
        # NEW: Send DM notification to the vouched user
        # ============================================
        try:
            embed = discord.Embed(
                title="🎉 You've received a vouch!",
                description=f"**{ctx.author.display_name}** vouched for you in {ctx.guild.name}",
                color=discord.Color.green()
            )
            embed.add_field(name="Reason", value=reason[:1024], inline=False)
            embed.add_field(name="Total Vouches", value=new_count)
            embed.set_footer(text=f"Vouched at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
            
            await member.send(embed=embed)
        except discord.Forbidden:
            # User has DMs disabled or blocked the bot - silently fail
            pass
        except Exception as e:
            print(f"Failed to send vouch DM: {e}")
        # ============================================
        
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
    """[ADMIN] Reset a user's vouches and allow re-vouching"""
    with get_db() as conn:
        # Reset vouch count
        conn.execute("UPDATE vouches SET vouch_count = 0 WHERE user_id = ?", (member.id,))
        # Clear vouch history
        conn.execute("DELETE FROM vouch_records WHERE vouched_id = ?", (member.id,))
        # Clear cooldowns (NEW)
        conn.execute("DELETE FROM vouch_cooldowns WHERE user_id = ?", (member.id,))
    
    await update_nickname(member)
    await ctx.send(f"♻️ Completely reset vouches for {member.mention}! Users can now vouch for them again.")


@bot.command()
@commands.check(is_admin)
async def clearvouches_all(ctx):
    """[ADMIN] Reset ALL vouches and cooldowns"""
    with get_db() as conn:
        # Reset all counts
        conn.execute("UPDATE vouches SET vouch_count = 0")
        # Clear all records
        conn.execute("DELETE FROM vouch_records")
        # Clear all cooldowns (NEW)
        conn.execute("DELETE FROM vouch_cooldowns")
    
    # Update nicknames
    for member in ctx.guild.members:
        if is_tracking_enabled(member.id):
            await update_nickname(member)
    
    await ctx.send("♻️ Completely reset ALL vouches and cooldowns!")

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
async def fix_vouch_records(ctx):
    """[ADMIN] Reconcile all vouch counts with records"""
    fixed = 0
    users = db_fetchall("SELECT user_id, vouch_count FROM vouches")
    for user in users:
        records = db_fetchone("SELECT COUNT(*) FROM vouch_records WHERE vouched_id = ?", (user['user_id'],))[0]
        diff = user['vouch_count'] - records
        
        if diff > 0:
            # Add missing admin vouches
            db_execute("INSERT INTO vouch_records (voucher_id, vouched_id) VALUES (?, ?)", 
                      (ctx.author.id, user['user_id']))
            fixed += diff
        elif diff < 0:
            # Remove excess vouches
            db_execute("""
            DELETE FROM vouch_records 
            WHERE rowid IN (
                SELECT rowid FROM vouch_records 
                WHERE vouched_id = ? 
                ORDER BY rowid DESC 
                LIMIT ?
            )
            """, (user['user_id'], abs(diff)))
            fixed += abs(diff)
    
    await ctx.send(f"✅ Fixed {fixed} vouch record mismatches!")

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
    """[ADMIN] Set vouch count with timestamp tracking"""
    current = get_vouches(member.id)
    difference = count - current
    current_time = int(time.time())
    
    try:
        with get_db() as conn:
            # Update main count
            conn.execute("""
                INSERT OR REPLACE INTO vouches 
                VALUES (?, ?, 1)
                """, (member.id, count))
            
            # Handle adjustments
            if difference > 0:
                # Insert with timestamps
                conn.executemany("""
                    INSERT OR IGNORE INTO vouch_records 
                    (voucher_id, vouched_id, timestamp)
                    VALUES (?, ?, ?)
                    """, [(ctx.author.id, member.id, current_time)] * difference)
            elif difference < 0:
                # Delete oldest vouches first
                conn.execute("""
                    DELETE FROM vouch_records 
                    WHERE rowid IN (
                        SELECT rowid FROM vouch_records 
                        WHERE vouched_id = ?
                        ORDER BY timestamp ASC, rowid ASC
                        LIMIT ?
                    )
                    """, (member.id, abs(difference)))
        
        await update_nickname(member)
        await ctx.send(f"✅ Set {member.mention}'s vouches to {count}")
    except sqlite3.Error as e:
        await ctx.send(f"❌ Database error: {str(e)}")
        print(f"Setvouches error: {traceback.format_exc()}")

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
@commands.check(is_admin)
async def reconcile_vouches(ctx, member: discord.Member = None):
    """[ADMIN] Fix vouch record mismatches safely"""
    try:
        if member:
            # Single user reconciliation
            vouch_count = get_vouches(member.id)
            records = db_fetchone("SELECT COUNT(*) FROM vouch_records WHERE vouched_id = ?", (member.id,))[0]
            
            if vouch_count > records:
                needed = vouch_count - records
                db_execute("""
                    INSERT OR IGNORE INTO vouch_records 
                    SELECT DISTINCT ?, ? 
                    WHERE NOT EXISTS (
                        SELECT 1 FROM vouch_records 
                        WHERE voucher_id = ? AND vouched_id = ?
                    )
                    LIMIT ?
                    """, (ctx.author.id, member.id, ctx.author.id, member.id, needed))
                await ctx.send(f"✅ Added {needed} admin records for {member.mention}")
            else:
                await ctx.send(f"ℹ️ {member.mention}'s records are correct")
        else:
            # Full server reconciliation
            fixed = 0
            users = db_fetchall("SELECT user_id, vouch_count FROM vouches WHERE vouch_count > 0")
            
            for user in users:
                records = db_fetchone("SELECT COUNT(*) FROM vouch_records WHERE vouched_id = ?", (user['user_id'],))[0]
                if records < user['vouch_count']:
                    needed = user['vouch_count'] - records
                    db_execute("""
                        INSERT OR IGNORE INTO vouch_records 
                        SELECT DISTINCT ?, ? 
                        WHERE NOT EXISTS (
                            SELECT 1 FROM vouch_records 
                            WHERE voucher_id = ? AND vouched_id = ?
                        )
                        LIMIT ?
                        """, (ctx.author.id, user['user_id'], ctx.author.id, user['user_id'], needed))
                    fixed += needed
            
            await ctx.send(f"✅ Fixed {fixed} vouch record mismatches")
    except sqlite3.Error as e:
        await ctx.send(f"❌ Database error during reconciliation: {str(e)}")

@bot.command()
@commands.check(is_admin)
async def vouch_history(ctx, member: discord.Member, limit: int = 5):
    """[ADMIN] Show recent vouch activity for a user"""
    records = db_fetchall("""
        SELECT vr.voucher_id, vr.timestamp, uu.user_id IS NOT NULL as is_admin, vr2.reason
        FROM vouch_records vr
        LEFT JOIN unvouchable_users uu ON vr.voucher_id = uu.user_id
        LEFT JOIN vouch_reasons vr2 ON vr.voucher_id = vr2.voucher_id AND vr.vouched_id = vr2.vouched_id
        WHERE vr.vouched_id = ?
        ORDER BY vr.timestamp DESC
        LIMIT ?
    """, (member.id, limit))

    if not records:
        return await ctx.send(f"No vouch history found for {member.mention}")

    lines = []
    for record in records:
        admin = ctx.guild.get_member(record['voucher_id'])
        admin_name = admin.mention if admin else f"Unknown User ({record['voucher_id']})"
        timestamp = datetime.datetime.fromtimestamp(record['timestamp']).strftime('%Y-%m-%d %H:%M')
        lines.append(
            f"{timestamp} - {admin_name} "
            f"{'(ADMIN) ' if record['is_admin'] else ''}"
            f"- Reason: {record['reason'] or 'None'}"
        )

    await ctx.send(
        f"**Last {limit} vouches for {member.mention}:**\n"
        + "\n".join(lines)
    )

@bot.command()
@commands.check(is_admin)
async def fix_vouch_timestamps(ctx):
    """[ADMIN] Repair missing timestamps in old records"""
    count = db_execute("""
        UPDATE vouch_records 
        SET timestamp = ?
        WHERE timestamp = 0 OR timestamp IS NULL
    """, (int(time.time()),))
    
    await ctx.send(f"✅ Updated timestamps for {count} records")


@bot.command()
async def vouch_sources(ctx, member: discord.Member):
    """Check where a user's vouches came from"""
    vouchers = db_fetchall("""
    SELECT voucher_id, COUNT(*) as count 
    FROM vouch_records 
    WHERE vouched_id = ?
    GROUP BY voucher_id
    """, (member.id,))
    
    if not vouchers:
        return await ctx.send(f"❌ No vouch records found for {member.mention}")
    
    lines = []
    for v in vouchers:
        user = ctx.guild.get_member(v['voucher_id'])
        name = user.mention if user else f"Unknown User ({v['voucher_id']})"
        lines.append(f"{name}: {v['count']} vouches")
    
    await ctx.send(
        f"**Vouch Sources for {member.mention}**\n" +
        "\n".join(lines)[:2000]
    )

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
    """Verify vouch count with admin vouch context"""
    target = member or ctx.author
    
    # 1. Get all data in one query
    with get_db() as conn:
        data = conn.execute("""
            SELECT 
                v.vouch_count,
                COUNT(vr.voucher_id) as total_vouches,
                SUM(CASE WHEN uu.user_id IS NOT NULL THEN 1 ELSE 0 END) as admin_vouches,
                MAX(vr.timestamp) as last_vouch_time,
                v.tracking_enabled,
                EXISTS(SELECT 1 FROM unvouchable_users WHERE user_id = v.user_id) as is_unvouchable
            FROM vouches v
            LEFT JOIN vouch_records vr ON vr.vouched_id = v.user_id
            LEFT JOIN unvouchable_users uu ON vr.voucher_id = uu.user_id
            WHERE v.user_id = ?
            GROUP BY v.user_id
            """, (target.id,)).fetchone()

    # 2. Parse data
    vouch_count = data[0] if data else 0
    total_vouches = data[1] if data else 0
    admin_vouches = data[2] if data else 0
    last_vouch_time = data[3] if data else 0
    tracking_enabled = data[4] if data else False
    is_unvouchable = data[5] if data else False
    
    community_vouches = total_vouches - admin_vouches
    admin_adjustments = max(0, vouch_count - total_vouches)
    
    # 3. Check nickname tags
    displayed_vouches = 0
    if target.display_name:
        match = re.search(r'[\[［](\d+)V[\]］]', target.display_name)
        if match:
            displayed_vouches = int(match.group(1))

    # 4. Build response
    response = [
        f"**Verification for {target.mention}**",
        f"• Displayed: {displayed_vouches}V",
        f"• Database: {vouch_count} vouches",
        f"┣ Community: {community_vouches}",
        f"┣ Admin: {admin_vouches}",
        f"┗ Adjustments: {admin_adjustments}",
    ]

    # 5. Determine status
    if is_unvouchable:
        status = "🔒 UNVOUCHABLE"
    elif not tracking_enabled:
        status = "⚙️ TRACKING OFF"
    elif displayed_vouches > vouch_count:
        status = "🚨 FAKE TAGS"
        await notify_admins(ctx.guild, target, 
            f"⚠️ Fake Tags Detected\n"
            f"Shows: {displayed_vouches}V\n"
            f"Actual: {vouch_count} vouches"
        )
    elif admin_adjustments > 0:
        # Differentiate between recent admin actions and old adjustments
        days_since_adjustment = (time.time() - last_vouch_time)/86400 if last_vouch_time else 999
        
        if days_since_adjustment < 7:  # Recent admin action
            status = f"🛡️ {admin_adjustments} ADMIN-SET (Recent)"
            response.append(f"• Last adjusted: {days_since_adjustment:.1f} days ago")
        else:  # Historical/admin-approved
            status = f"🛡️ {admin_adjustments} ADMIN-SET (Legacy)"
    else:
        status = "✅ VERIFIED"

    response.append(f"• Status: {status}")
    await ctx.send("\n".join(response))

async def notify_admins(guild, member, reason):
    """Send alerts to admins via DM or staff channel"""
    _, admin_roles_id = get_config(guild.id)
    recipients = list({m for role in guild.roles
                        if role.id in admin_roles_id
                        for m in role.members
                        if not m.bot})

    # Get the staff channel
    _, admin_roles = get_config(guild.id)
    staff_channel = get_staff_channel(guild)

    
    embed = discord.Embed(
        title="🚨 Vouch Verification Alert",
        color=discord.Color.red()
    )
    embed.add_field(name="Member", value=member.mention, inline=False)
    embed.add_field(name="Issue", value=reason, inline=False)
    embed.add_field(name="Action Required", value="Please verify and respond with ✅ to reset or ❌ to ignore", inline=False)
    
    # Try DMing each admin
    notified = False
    for admin in recipients:
        try:
            msg = await admin.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            
            # Track this notification
            if not hasattr(bot, 'discrepancy_notifications'):
                bot.discrepancy_notifications = {}
            bot.discrepancy_notifications[msg.id] = {
                'admin_id': admin.id,
                'member_id': member.id,
                'timestamp': time.time()
            }
            notified = True
        except discord.Forbidden:
            continue
    
    # Fallback to staff channel if DMs failed
    if not notified and staff_channel:
        try:
            msg = await staff_channel.send(
                content=" ".join(m.mention for m in recipients),
                embed=embed
            )
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            
            # Track channel notification differently
            bot.discrepancy_notifications[msg.id] = {
                'admin_id': guild.me.id,  # Mark as channel message
                'member_id': member.id,
                'timestamp': time.time()
            }
        except discord.Forbidden:
            print(f"Failed to send to {STAFF_CHANNEL_NAME}")
        except discord.HTTPException as e:
            print(f"Channel notification failed: {e}")

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
            # Send to both the original channel and admin alerts channel
            await ctx.send("Database backup created successfully!")
            alert_channel = bot.get_channel(ADMIN_ALERTS_CHANNEL_ID)
            if alert_channel:
                await alert_channel.send(
                    f"Database backup requested by {ctx.author.mention} (ID: {ctx.author.id}):",
                    file=discord.File(f, 'vouches_backup.db')
                )
            else:
                await ctx.send("⚠️ Could not find admin alerts channel, but backup was created.")
    except Exception as e:
        error_msg = f"❌ Backup failed: {str(e)}"
        await ctx.send(error_msg)
        # Try to send error to admin channel too
        try:
            alert_channel = bot.get_channel(ADMIN_ALERTS_CHANNEL_ID)
            if alert_channel:
                await alert_channel.send(error_msg)
        except:
            pass

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    bot.add_view(VouchButtonView(bot))
    bot.loop.create_task(clean_old_notifications())

    for guild in bot.guilds:
        staff_channel_name, admin_roles = get_config(guild.id)

        # Check staff channel
        channel = discord.utils.get(guild.text_channels, name=staff_channel_name)
        missing_channel = channel is None

        # Check admin roles
        missing_roles = [rid for rid in admin_roles if guild.get_role(rid) is None]

        # DM owner
        if missing_channel or missing_roles:
            try:
                owner = guild.owner
                msg = "**⚠️ VouchBot Configuration Warning**\n"
                if missing_channel:
                    msg += f"• Staff channel `{staff_channel_name}` not found.\n"
                if missing_roles:
                    msg += f"• Missing admin roles: `{', '.join(missing_roles)}`\n"
                msg += "Use `!setconfig` to update them."
                await owner.send(msg)
            except Exception as e:
                print(f"Failed to DM owner in {guild.name}: {e}")

@bot.event
async def on_command_error(ctx, error):
    # Command Not Found - Smart Suggestions
    if isinstance(error, commands.CommandNotFound):
        # Get available commands user can run
        available_commands = []
        for cmd in bot.commands:
            try:
                if await cmd.can_run(ctx):
                    available_commands.append(cmd.name)
            except:
                continue
        
        # Find similar commands
        invoked = ctx.invoked_with.lower()
        suggestions = []
        
        # Check admin commands first if user is admin
        if is_admin(ctx):
            admin_commands = [cmd.name for cmd in bot.commands if cmd.checks]
            suggestions.extend(
                cmd for cmd in admin_commands 
                if cmd.startswith(invoked[:3])  # Match first 3 letters
            )
        
        # Check regular commands
        regular_commands = [cmd.name for cmd in bot.commands if not cmd.checks]
        suggestions.extend(
            cmd for cmd in regular_commands
            if cmd.startswith(invoked[:3])
        )
        
        # Remove duplicates and the failed command itself
        suggestions = list(set(suggestions) - {invoked})
        
        # Build response
        if suggestions:
            response = f"❌ Command `!{invoked}` not found. Did you mean:\n"
            response += "\n".join(f"• `!{cmd}`" for cmd in suggestions[:3])  # Max 3 suggestions
        else:
            response = f"❌ Command `!{invoked}` not found. Use `!help` for available commands."
        
        await ctx.send(response)
        return
    
    # Special case for !myroles typo (keep your original behavior)
    if ctx.invoked_with == "myroles":
        await ctx.send("❌ Command not found. Did you mean `!myvouches`?")
        return
    
    # Missing Permissions
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
        return
    
    # Bad Arguments (e.g., invalid number)
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Invalid argument: {str(error)}")
        return
    
    # Log unexpected errors to admin channel
    error_channel = bot.get_channel(ADMIN_ALERTS_CHANNEL_ID)  # Make sure this exists!
    if error_channel:
        await error_channel.send(
            f"⚠️ **Error in `{ctx.command or 'N/A'}`**\n"
            f"• User: {ctx.author.mention}\n"
            f"• Error: ```{str(error)[:1000]}```\n"
            f"[Jump to Message]({ctx.message.jump_url})"
        )
    
    # Print to console for debugging
    print(f"[ERROR] {type(error)}: {error}")

@bot.event
async def on_raw_reaction_add(payload):

    if not hasattr(bot, 'discrepancy_notifications'):
        return
    
    if payload.message_id not in bot.discrepancy_notifications:
        return
    
    # Skip bot's own reactions
    if payload.user_id == bot.user.id:
        return
    
    try:
        data = bot.discrepancy_notifications[payload.message_id]
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return

        _, admin_roles = get_config(guild.id)
        
        # Get the member in question
        member = guild.get_member(data['member_id'])
        if not member:
            return
        
        # Check if reaction is from admin
        reactor = guild.get_member(payload.user_id)
        if not reactor or not any(r.id in admin_roles for r in reactor.roles):
            return

        # Handle the action
        if str(payload.emoji) == "✅":
            # Reset vouches
            db_execute("UPDATE vouches SET vouch_count = 0 WHERE user_id = ?", (member.id,))
            db_execute("DELETE FROM vouch_records WHERE vouched_id = ?", (member.id,))
            
            # Clean nickname
            try:
                await member.edit(nick=clean_nickname(member.display_name))
            except discord.HTTPException:
                pass
            
            # Send confirmation where it came from
            if data['admin_id'] == guild.me.id:  # Staff channel
                channel = guild.get_channel(payload.channel_id)
                if channel:
                    await channel.send(f"✅ {reactor.mention} reset vouches for {member.mention}")
            else:  # DM
                try:
                    await reactor.send(f"✅ Reset vouches for {member.mention}")
                except discord.Forbidden:
                    pass
        
        # Clean up
        del bot.discrepancy_notifications[payload.message_id]
        
    except Exception as e:
        print(f"Reaction handling error: {e}")
        if payload.message_id in bot.discrepancy_notifications:
            del bot.discrepancy_notifications[payload.message_id]

keep_alive()
bot.run(TOKEN)
