import os
import json
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import OpenAI

# ===== CONFIG =====
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ===== FILE =====
DATA_FILE = "data.json"
ADMIN_FILE = "admins.json"
BANNED_FILE = "banned.json"

# ===== LOAD SAVE =====
def load_json(file):
    try:
        if not os.path.exists(file):
            return {}
        with open(file, "r") as f:
            return json.loads(f.read() or "{}")
    except:
        return {}

def save_json(file, data):
    try:
        with open(file, "w") as f:
            json.dump(data, f)
    except:
        pass

def load_list(file):
    try:
        if not os.path.exists(file):
            return set()
        with open(file, "r") as f:
            return set(json.load(f))
    except:
        return set()

def save_list(file, data):
    try:
        with open(file, "w") as f:
            json.dump(list(data), f)
    except:
        pass

keywords = load_json(DATA_FILE)
ADMIN_IDS = load_list(ADMIN_FILE)
BANNED_IDS = load_list(BANNED_FILE)

user_state = {}

# ===== PERMISSION =====
def is_admin(uid):
    return uid == OWNER_ID or uid in ADMIN_IDS

def is_banned(uid):
    return uid in BANNED_IDS

# ===== BUTTON =====
def build_buttons(text):
    if not text:
        return None

    rows, row = [], []

    for line in text.split("\n"):
        if "|" not in line:
            continue
        try:
            name, url = line.split("|", 1)
            name, url = name.strip(), url.strip()
            if not url.startswith("http"):
                continue

            row.append(InlineKeyboardButton(text=name, url=url))

            if len(row) == 2:
                rows.append(row)
                row = []
        except:
            continue

    if row:
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

def menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Thêm", callback_data="add")],
        [InlineKeyboardButton(text="📋 Danh sách", callback_data="list")],
        [InlineKeyboardButton(text="✏️ Sửa", callback_data="edit")],
        [InlineKeyboardButton(text="❌ Xóa", callback_data="delete")],
        [InlineKeyboardButton(text="👁 Preview", callback_data="preview")]
    ])

def skip_btn(tag):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Bỏ qua", callback_data=tag)]
    ])

# ===== AI =====
async def ask_ai(text):
    if not client:
        return "🤖 AI off"
    try:
        r = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Trả lời ngắn gọn"},
                {"role": "user", "content": text}
            ]
        )
        return r.choices[0].message.content
    except:
        return "⚠️ AI lỗi"

# ===== START =====
@dp.message(Command("start"))
async def start(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer("🚀 CONTROL PANEL", reply_markup=menu())

# ===== CALLBACK =====
@dp.callback_query()
async def cb(c: types.CallbackQuery):
    uid = c.from_user.id

    if is_banned(uid) or not is_admin(uid):
        return

    if c.data == "add":
        user_state[uid] = {"step": "keyword"}
        return await c.message.edit_text("🔑 Nhập keyword")

    if c.data == "list":
        return await c.message.edit_text(
            "\n".join(keywords.keys()) or "❌ Trống",
            reply_markup=menu()
        )

    if c.data == "delete":
        user_state[uid] = {"step": "delete"}
        return await c.message.edit_text("Nhập keyword cần xóa")

    if c.data == "edit":
        user_state[uid] = {"step": "edit"}
        return await c.message.edit_text("Nhập keyword cần sửa")

    if c.data == "preview":
        user_state[uid] = {"step": "preview"}
        return await c.message.edit_text("Nhập keyword")

    if c.data == "skip_image":
        user_state[uid]["image"] = None
        user_state[uid]["step"] = "button"
        return await c.message.edit_text("🔗 Nhập nút", reply_markup=skip_btn("skip_button"))

    if c.data == "skip_button":
        s = user_state[uid]
        key = s.get("edit") or s["keyword"]

        keywords[key] = {
            "text": s["text"],
            "image": s.get("image"),
            "button": None
        }

        save_json(DATA_FILE, keywords)
        user_state.pop(uid)
        return await c.message.edit_text("✅ Lưu xong", reply_markup=menu())

# ===== MAIN =====
@dp.message()
async def handle(m: types.Message):
    try:
        uid = m.from_user.id

        if is_banned(uid) or not is_admin(uid):
            return

        text = (m.text or "").strip()

        if uid in user_state:
            s = user_state[uid]

            if s["step"] == "delete":
                keywords.pop(text.lower(), None)
                save_json(DATA_FILE, keywords)
                user_state.pop(uid)
                return await m.answer("🗑 Xóa xong", reply_markup=menu())

            if s["step"] == "edit":
                if text.lower() in keywords:
                    user_state[uid] = {"step": "keyword", "edit": text.lower()}
                    return await m.answer("Nhập nội dung mới")
                return await m.answer("❌ Không có")

            if s["step"] == "preview":
                d = keywords.get(text.lower())
                if not d:
                    return await m.answer("❌ Không tồn tại")

                markup = build_buttons(d.get("button"))

                if d.get("image"):
                    msg = await m.answer_photo(d["image"], caption=d["text"], reply_markup=markup)
                else:
                    msg = await m.answer(d["text"], reply_markup=markup)

                await asyncio.sleep(5)
                try: await msg.delete()
                except: pass

                user_state.pop(uid)
                return

            if s["step"] == "keyword":
                s["keyword"] = text.lower()
                s["step"] = "text"
                return await m.answer("📝 Nhập nội dung")

            if s["step"] == "text":
                s["text"] = text
                s["step"] = "image"
                return await m.answer("🖼 Gửi ảnh hoặc bỏ qua", reply_markup=skip_btn("skip_image"))

            if s["step"] == "image":
                if m.photo:
                    s["image"] = m.photo[-1].file_id
                    s["step"] = "button"
                    return await m.answer("🔗 Nhập nút", reply_markup=skip_btn("skip_button"))
                return await m.answer("Gửi ảnh hoặc bấm bỏ qua", reply_markup=skip_btn("skip_image"))

            if s["step"] == "button":
                s["button"] = text
                key = s.get("edit") or s["keyword"]

                keywords[key] = {
                    "text": s["text"],
                    "image": s.get("image"),
                    "button": s["button"]
                }

                save_json(DATA_FILE, keywords)
                user_state.pop(uid)
                return await m.answer("✅ Lưu", reply_markup=menu())

        # keyword
        if text.lower() in keywords:
            d = keywords[text.lower()]
            markup = build_buttons(d.get("button"))

            if d.get("image"):
                return await m.answer_photo(d["image"], caption=d["text"], reply_markup=markup)
            return await m.answer(d["text"], reply_markup=markup)

        # AI chỉ admin
        return await m.answer(await ask_ai(text))

    except Exception as e:
        print("ERR:", e)

# ===== WEB DASHBOARD =====
async def admin_page(request):
    html = f"""
    <html>
    <body style="background:#0f172a;color:white;font-family:sans-serif;padding:20px">
    <h1>🚀 CONTROL PANEL</h1>

    <h3>👑 OWNER</h3>
    <p>{OWNER_ID}</p>

    <h3>🛡 ADMIN</h3>
    {''.join([f"<p>{a} <a href='/del_admin?id={a}'>❌</a></p>" for a in ADMIN_IDS])}

    <form action="/add_admin">
        <input name="id" placeholder="User ID">
        <button>Thêm</button>
    </form>

    <h3>🚫 BANNED</h3>
    {''.join([f"<p>{b} <a href='/unban?id={b}'>✅</a></p>" for b in BANNED_IDS])}

    <form action="/ban">
        <input name="id" placeholder="User ID">
        <button>Ban</button>
    </form>

    </body>
    </html>
    """
    return web.Response(text=html, content_type="text/html")

async def add_admin(request):
    uid = int(request.query.get("id"))
    if uid != OWNER_ID:
        ADMIN_IDS.add(uid)
        save_list(ADMIN_FILE, ADMIN_IDS)
    raise web.HTTPFound("/admin")

async def del_admin(request):
    uid = int(request.query.get("id"))
    ADMIN_IDS.discard(uid)
    save_list(ADMIN_FILE, ADMIN_IDS)
    raise web.HTTPFound("/admin")

async def ban_user(request):
    uid = int(request.query.get("id"))
    if uid != OWNER_ID:
        BANNED_IDS.add(uid)
        save_list(BANNED_FILE, BANNED_IDS)
    raise web.HTTPFound("/admin")

async def unban_user(request):
    uid = int(request.query.get("id"))
    BANNED_IDS.discard(uid)
    save_list(BANNED_FILE, BANNED_IDS)
    raise web.HTTPFound("/admin")

# ===== WEB =====
app = web.Application()
app.router.add_get("/", lambda r: web.Response(text="BOT OK"))
app.router.add_get("/admin", admin_page)
app.router.add_get("/add_admin", add_admin)
app.router.add_get("/del_admin", del_admin)
app.router.add_get("/ban", ban_user)
app.router.add_get("/unban", unban_user)

async def start_bot(app):
    asyncio.create_task(dp.start_polling(bot))

app.on_startup.append(start_bot)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
