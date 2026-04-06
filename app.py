import os
import logging
import psycopg2
import redis
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import (Updater, CommandHandler, CallbackQueryHandler,
                          CallbackContext, MessageHandler, Filters)

# ---- ENV VAR ----
TOKEN      = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
PG_URL     = os.environ.get("DATABASE_URL", "postgres://....")
REDIS_URL  = os.environ.get("REDIS_URL", "redis://....")

logging.basicConfig(level=logging.INFO)

# ---- DB ----
def get_pg_conn():
    return psycopg2.connect(PG_URL, sslmode='require')
r = redis.from_url(REDIS_URL)

# USERS & ADMIN
def add_user(user):
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING;
            """, (user.id, user.username, user.first_name, user.last_name))
            conn.commit()
    finally:
        conn.close()

def set_admin(user_id):
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO admins (user_id) VALUES (%s) ON CONFLICT DO NOTHING;", (user_id,))
            conn.commit()
    finally:
        conn.close()
def get_admin_ids():
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM admins;")
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
def is_admin(user_id):
    return user_id in get_admin_ids()

# BANNED WORDS
def get_banned_words():
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT word FROM banned_words;")
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
def add_banned_word(word):
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO banned_words (word) VALUES (%s) ON CONFLICT DO NOTHING;", (word,))
            conn.commit()
    finally:
        conn.close()
def remove_banned_word(word):
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM banned_words WHERE word=%s;", (word,))
            conn.commit()
    finally:
        conn.close()

# Đếm tin nhắn
def count_msg(user_id):
    key = f"user:{user_id}:count"
    return r.incr(key)

# ------ NÚT MENU ------ #
def full_menu(is_admin=False):
    btns = [
        [
            InlineKeyboardButton("📄 Hướng dẫn", callback_data="guide"),
            InlineKeyboardButton("🔗 Đăng ký", callback_data="register")
        ],
        [
            InlineKeyboardButton("📋 Danh sách từ cấm", callback_data="listban"),
            InlineKeyboardButton("📊 Thống kê", callback_data="stat")
        ]
    ]
    if is_admin:
        btns.append([
            InlineKeyboardButton("➕ Thêm từ cấm", callback_data="addban"),
            InlineKeyboardButton("🚫 Xóa từ cấm", callback_data="delban"),
        ])
    btns.append([InlineKeyboardButton("🔒 Đóng menu", callback_data="close")])
    return InlineKeyboardMarkup(btns)
def make_back_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Quay lại Menu", callback_data="menu")]])

# ------ LUỒNG CHÍNH ------ #
def menu(update: Update, context: CallbackContext):
    admin = is_admin(update.effective_user.id)
    reply_markup = full_menu(admin)
    text = "🎛 <b>MENU QUẢN TRỊ NHÓM:</b>"
    if update.callback_query:
        update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    else:
        update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

def buttons(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    query = update.callback_query
    data = query.data

    if data == "menu":
        menu(update, context)
    elif data == "close":
        query.edit_message_text("🔒 Menu đã đóng.")
    elif data == "guide":
        query.edit_message_text(
            "🤖 <b>Bot quản trị nhóm toàn nút:</b>"
            "\n- Bấm <b>Thêm từ cấm</b> để thêm"
            "\n- Bấm <b>Xóa từ cấm</b> để xóa"
            "\n- Xem hướng dẫn, top, danh sách cấm đều bằng nút.",
            reply_markup=full_menu(is_admin(user_id)),
            parse_mode=ParseMode.HTML,
        )
    elif data == "register":
        query.edit_message_text("📝 Đăng ký tại: https://yourwebsite.com/register", 
                               reply_markup=full_menu(is_admin(user_id)), parse_mode=ParseMode.HTML)
    elif data == "listban":
        words = get_banned_words()
        txt = "\n".join(words) if words else "Chưa có từ cấm nào."
        query.edit_message_text("📋 <b>Danh sách từ cấm:</b>\n" + txt, reply_markup=make_back_menu(), parse_mode=ParseMode.HTML)
    elif data == "addban" and is_admin(user_id):
        query.edit_message_text("✏️ Gửi từ khoá bạn muốn cấm dưới tin nhắn này.", reply_markup=make_back_menu())
        context.user_data['awaiting_add_ban'] = True
    elif data == "delban" and is_admin(user_id):
        words = get_banned_words()
        if not words:
            query.edit_message_text("⛔ Chưa có từ cấm nào!", reply_markup=make_back_menu())
            return
        btns = [
            [InlineKeyboardButton(word, callback_data=f"del_{word}")]
            for word in words
        ]
        btns.append([InlineKeyboardButton("🔙 Quay lại Menu", callback_data="menu")])
        query.edit_message_text("🚫 <b>BẤM vào từ bạn muốn xoá:</b>", reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)
    elif data.startswith("del_") and is_admin(user_id):
        word = data[4:]
        remove_banned_word(word)
        query.answer(f"Đã xoá '{word}'")
        words = get_banned_words()
        txt = "\n".join(words) if words else "(Đã xoá hết từ cấm!)"
        query.edit_message_text(
            f"☑️ Đã xoá <b>{word}</b>.\nCòn lại:\n{txt}",
            reply_markup=make_back_menu(), parse_mode=ParseMode.HTML
        )
    elif data == "stat":
        user_msgs = []
        for k in r.scan_iter("user:*:count"):
            uid = k.decode().split(":")[1]
            cnt = int(r.get(k))
            user_msgs.append((uid, cnt))
        top = sorted(user_msgs, key=lambda x: x[1], reverse=True)[:5]
        txt = "\n".join([f"<code>{uid}</code>: {cnt} tin" for uid, cnt in top]) if top else "Chưa có dữ liệu."
        query.edit_message_text("📊 <b>Top Gửi Tin:</b>\n" + txt, reply_markup=make_back_menu(), parse_mode=ParseMode.HTML)
    else:
        query.answer("Không rõ thao tác hoặc bạn không phải admin.")
