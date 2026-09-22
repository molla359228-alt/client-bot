import logging
import re
import sqlite3
import os
from collections import OrderedDict
from datetime import datetime
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
import pytz

# ================== কনফিগারেশন ==================
BOT_TOKEN = "8831118670:AAEmmtkN7uOfMDYM-w72H2gj518zCztptug"
OWNER_ID  = 7495790113

BD_TZ = pytz.timezone("Asia/Dhaka")
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clients.db")

# ================== 🌐 Render-এর জন্য ডামি HTTP সার্ভার ==================
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "✅ Number Check Bot is running!", 200

@web_app.route('/health')
def health():
    return "OK", 200

def run_web():
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"Starting web server on port {port}...")
    web_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ================== হেল্পার ==================
def now_bd():
    return datetime.now(BD_TZ)

def fmt_dt(dt):
    return dt.strftime("%d %b %Y, %I:%M %p")

def fmt_date(dt):
    return dt.strftime("%Y-%m-%d")

def fmt_date_display(date_str):
    """YYYY-MM-DD -> 23 Sep 2026 (দেখানোর জন্য সুন্দর ফরম্যাট)"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%d %b %Y")
    except (ValueError, TypeError):
        return date_str or "অজানা তারিখ"

def is_owner(user_id):
    return user_id == OWNER_ID

def normalize(value):
    """নাম্বার/ইউজারনেম নরমালাইজ - @, +88, স্পেস, ড্যাশ বাদ"""
    if not value:
        return ""
    v = value.strip().lower()
    v = v.lstrip("@")
    v = v.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if v.startswith("+880"):
        v = v[4:]
    elif v.startswith("880"):
        v = v[3:]
    elif v.startswith("+88"):
        v = v[3:]
    elif v.startswith("88") and len(v) > 11:
        v = v[2:]
    return v

def is_valid_input(text):
    if not text:
        return False
    text = text.strip()
    if len(text) > 50 or len(text) < 2:
        return False

    if text.startswith("@"):
        username = text[1:]
        if 2 <= len(username) <= 30:
            if re.match(r'^[a-zA-Z0-9_.]+$', username):
                return True

    if re.match(r'^[a-zA-Z0-9_.]{2,30}$', text):
        if re.match(r'^\d+$', text):
            pass
        else:
            return True

    digits_only = re.sub(r'[^\d]', '', text)
    if len(digits_only) >= 10 and len(digits_only) <= 15:
        if re.match(r'^[\+\d\s\-\(\)\.]{10,25}$', text):
            return True

    return False

# ================== ডেটাবেজ ==================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            value TEXT NOT NULL,
            normalized TEXT NOT NULL UNIQUE,
            client_name TEXT,
            added_by INTEGER,
            added_by_name TEXT,
            added_at TEXT,
            added_date TEXT
        )
    """)
    conn.commit()

    # পুরনো ডেটাবেজে added_date কলাম না থাকলে যোগ করে দাও (migration)
    c.execute("PRAGMA table_info(clients)")
    existing_columns = [row[1] for row in c.fetchall()]
    if "added_date" not in existing_columns:
        c.execute("ALTER TABLE clients ADD COLUMN added_date TEXT")
        conn.commit()

    conn.close()

def find_client(value):
    norm = normalize(value)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value, client_name, added_by_name, added_at FROM clients WHERE normalized = ?", (norm,))
    row = c.fetchone()
    conn.close()
    return row

def add_client(value, client_name, added_by, added_by_name):
    norm = normalize(value)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        now = now_bd()
        c.execute(
            "INSERT INTO clients (value, normalized, client_name, added_by, added_by_name, added_at, added_date) "
            "VALUES (?,?,?,?,?,?,?)",
            (value, norm, client_name, added_by, added_by_name, fmt_dt(now), fmt_date(now))
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def list_clients(limit=50):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value, client_name, added_by_name, added_at FROM clients ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows

def count_clients():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM clients")
    n = c.fetchone()[0]
    conn.close()
    return n

def get_daily_breakdown():
    """প্রতিটা তারিখে কোন স্টাফ কতগুলো ক্লায়েন্ট যোগ করেছে, তার raw ডেটা।"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT added_date, added_by_name, COUNT(*) as cnt
        FROM clients
        WHERE added_date IS NOT NULL AND added_date != ''
        GROUP BY added_date, added_by_name
        ORDER BY added_date DESC, cnt DESC
    """)
    rows = c.fetchall()
    conn.close()
    return rows

def get_staff_totals():
    """সব সময় মিলিয়ে প্রতিটা স্টাফ মোট কতগুলো ক্লায়েন্ট যোগ করেছে।"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT added_by_name, COUNT(*) as cnt
        FROM clients
        GROUP BY added_by_name
        ORDER BY cnt DESC
    """)
    rows = c.fetchall()
    conn.close()
    return rows

def get_user_name(uid, uname):
    return uname or f"User_{uid}"

# ================== রিপোর্ট টেক্সট তৈরি ==================
def build_daily_report_text(limit_days=14):
    rows = get_daily_breakdown()
    if not rows:
        return "📭 এখনো তারিখভিত্তিক কোনো ডেটা নেই।"

    grouped = OrderedDict()
    for date_str, staff_name, cnt in rows:
        grouped.setdefault(date_str, []).append((staff_name or "Unknown", cnt))

    lines = [f"📅 *দৈনিক রিপোর্ট* (সাম্প্রতিক {limit_days} দিন)\n"]
    for i, (date_str, staff_list) in enumerate(grouped.items()):
        if i >= limit_days:
            break
        total = sum(cnt for _, cnt in staff_list)
        lines.append(f"\n🗓 {fmt_date_display(date_str)} — মোট: *{total}*")
        for staff_name, cnt in staff_list:
            lines.append(f"   • {staff_name}: {cnt}")

    return "\n".join(lines)

def build_staff_report_text():
    rows = get_staff_totals()
    if not rows:
        return "📭 এখনো কোনো ডেটা যোগ হয়নি।"

    grand_total = sum(cnt for _, cnt in rows)
    lines = [f"🧑‍💼 *স্টাফ অনুযায়ী মোট যোগ করা ক্লায়েন্ট* (সর্বমোট: {grand_total})\n"]
    for i, (staff_name, cnt) in enumerate(rows, 1):
        lines.append(f"{i}. {staff_name or 'Unknown'}: {cnt}")

    return "\n".join(lines)

def send_long_text(text, limit=4000):
    if len(text) > limit:
        return text[:limit] + "\n\n... (আরও আছে)"
    return text

# ================== কীবোর্ড ==================
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Count", callback_data="count")],
        [InlineKeyboardButton("📋 List", callback_data="list_clients")],
        [InlineKeyboardButton("📅 Daily Report", callback_data="daily_report")],
        [InlineKeyboardButton("🧑‍💼 Staff Report", callback_data="staff_report")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ])

# ================== কমান্ড ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.message.reply_text(
        f"👋 স্বাগতম {u.first_name}!\n\n"
        f"🔎 শুধু ক্লায়েন্টের নাম্বার বা ইউজারনেম লিখুন — বট অটো চেক করে রিপ্লাই দেবে।\n\n"
        f"📌 উদাহরণ:\n"
        f"`@john123`\n"
        f"`@suliaeva.art`\n"
        f"`john.doe_123`\n"
        f"`01712345678`\n"
        f"`+8801712345678`\n\n"
        f"✅ নতুন হলে → Yes, can talk + অটো যোগ হবে\n"
        f"❌ পুরনো হলে → No, Don't talk\n\n"
        f"💡 কমান্ড:\n"
        f"/list — ক্লায়েন্ট লিস্ট (Owner)\n"
        f"/count — মোট ক্লায়েন্ট (Owner)\n"
        f"/report — দৈনিক রিপোর্ট, প্রতিদিন কে কতগুলো যোগ করেছে (Owner)\n"
        f"/staff — স্টাফ অনুযায়ী মোট হিসাব (Owner)",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ শুধু Owner এই কমান্ড ব্যবহার করতে পারবেন।")
        return
    rows = list_clients(50)
    if not rows:
        await update.message.reply_text("📭 এখনো কোনো ক্লায়েন্ট যোগ করা হয়নি।")
        return
    lines = ["📋 সাম্প্রতিক ৫০ জন ক্লায়েন্ট\n"]
    for i, (val, cname, added_by, added_at) in enumerate(rows, 1):
        lines.append(f"{i}. {val} — {cname or '—'}\n   ✍️ {added_by} | {added_at}")
    text = send_long_text("\n".join(lines))
    await update.message.reply_text(text)

async def cmd_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ শুধু Owner এই কমান্ড ব্যবহার করতে পারবেন।")
        return
    n = count_clients()
    await update.message.reply_text(f"📊 মোট ক্লায়েন্ট: *{n}*", parse_mode="Markdown")

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ শুধু Owner এই কমান্ড ব্যবহার করতে পারবেন।")
        return
    text = send_long_text(build_daily_report_text())
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ শুধু Owner এই কমান্ড ব্যবহার করতে পারবেন।")
        return
    text = send_long_text(build_staff_report_text())
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

# ================== মূল মেসেজ হ্যান্ডলার ==================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()

    if text.startswith("/"):
        return

    if not is_valid_input(text):
        return

    u = update.effective_user
    staff_name = get_user_name(u.id, u.first_name)

    row = find_client(text)

    if row:
        old_value, client_name, added_by, added_at = row
        await update.message.reply_text(
            f"❌ *No, Don't talk*\n"
            f"🕒 This is an OLD ID\n\n"
            f"📞 যা দিয়েছেন: `{text}`\n"
            f"🗂 ডেটাবেজে আছে: `{old_value}`\n"
            f"👤 ক্লায়েন্টের নাম: {client_name or 'অজানা'}\n"
            f"✍️ যোগ করেছেন: {added_by or 'অজানা'}\n"
            f"📅 তারিখ: {added_at}",
            parse_mode="Markdown"
        )
    else:
        ok = add_client(text, staff_name, u.id, staff_name)
        if ok:
            await update.message.reply_text(
                f"✅ *Yes, can talk*\n"
                f"🆕 This is a NEW client\n\n"
                f"📞 `{text}` — অটো ডেটাবেজে যোগ হয়েছে\n"
                f"✍️ যোগ করেছেন: {staff_name}\n"
                f"📅 তারিখ: {fmt_dt(now_bd())}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"✅ *Yes, can talk*\n"
                f"🆕 This is a NEW client\n\n"
                f"📞 `{text}`\n\n"
                f"⚠️ তবে অটো যোগ করা যায়নি।",
                parse_mode="Markdown"
            )

# ================== বাটন হ্যান্ডলার ==================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "count":
        n = count_clients()
        await q.message.reply_text(f"📊 মোট ক্লায়েন্ট: *{n}*", parse_mode="Markdown")

    elif data == "list_clients":
        if not is_owner(q.from_user.id):
            await q.message.reply_text("❌ শুধু Owner এই কমান্ড ব্যবহার করতে পারবেন।")
            return
        rows = list_clients(50)
        if not rows:
            await q.message.reply_text("📭 এখনো কোনো ক্লায়েন্ট যোগ করা হয়নি।")
            return
        lines = ["📋 সাম্প্রতিক ৫০ জন ক্লায়েন্ট\n"]
        for i, (val, cname, added_by, added_at) in enumerate(rows, 1):
            lines.append(f"{i}. {val} — {cname or '—'}\n   ✍️ {added_by} | {added_at}")
        text = send_long_text("\n".join(lines))
        await q.message.reply_text(text)

    elif data == "daily_report":
        if not is_owner(q.from_user.id):
            await q.message.reply_text("❌ শুধু Owner এই কমান্ড ব্যবহার করতে পারবেন।")
            return
        text = send_long_text(build_daily_report_text())
        await q.message.reply_text(text, parse_mode="Markdown")

    elif data == "staff_report":
        if not is_owner(q.from_user.id):
            await q.message.reply_text("❌ শুধু Owner এই কমান্ড ব্যবহার করতে পারবেন।")
            return
        text = send_long_text(build_staff_report_text())
        await q.message.reply_text(text, parse_mode="Markdown")

    elif data == "help":
        u = q.from_user
        await q.message.reply_text(
            f"👋 {u.first_name}!\n\n"
            f"🔎 শুধু নাম্বার বা ইউজারনেম লিখুন:\n"
            f"`@suliaeva.art`\n"
            f"`john.doe_123`\n"
            f"`01712345678`\n\n"
            f"বট অটো চেক করে রিপ্লাই দেবে।",
            parse_mode="Markdown"
        )

# ================== main ==================
def main():
    logging.basicConfig(level=logging.INFO)
    init_db()

    # 🌐 প্রথমে ডামি HTTP সার্ভার চালু
    logging.info("Starting web server for Render...")
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

    # 🤖 তারপর বট চালু
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("count", cmd_count))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("staff", cmd_staff))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Number Check Bot চালু হয়েছে...")
    app.run_polling()

if __name__ == "__main__":
    main()
