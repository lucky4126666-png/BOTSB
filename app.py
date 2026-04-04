import os, json, asyncio, uuid
from datetime import datetime, timedelta, timezone
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import OpenAI

# ===== LOCK =====
LOCK_FILE = "/tmp/bot.lock"
if os.path.exists(LOCK_FILE):
    print("⚠️ Bot already running → exit")
    exit()
open(LOCK_FILE, "w").close()

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "123456789"))
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", OWNER_ID))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "123456")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
client = OpenAI(api_key=OPENAI_API_KEY)

# ===== FILE =====
DATA_FILE = "data.json"
ADMIN_FILE = "admins.json"
BANNED_FILE = "banned.json"
SCHEDULE_FILE = "schedule.json"

# ===== LOAD =====
def load_json(file):
    try:
        if not os.path.exists(file): return {}
        with open(file) as f: return json.loads(f.read() or "{}")
    except: return {}

def save_json(file, data):
    with open(file,"w") as f: json.dump(data,f)

def load_list(file):
    try:
        if not os.path.exists(file): return set()
        with open(file) as f: return set(json.load(f))
    except: return set()

def save_list(file,data):
    with open(file,"w") as f: json.dump(list(data),f)

def load_schedule():
    try:
        if not os.path.exists(SCHEDULE_FILE): return []
        with open(SCHEDULE_FILE) as f: return json.load(f)
    except: return []

def save_schedule(data):
    with open(SCHEDULE_FILE,"w") as f: json.dump(data,f)

keywords = load_json(DATA_FILE)
ADMIN_IDS = load_list(ADMIN_FILE)
BANNED_IDS = load_list(BANNED_FILE)
schedules = load_schedule()

# ===== TIME =====
def get_now():
    return datetime.now(timezone.utc) + timedelta(hours=7)

# ===== SESSION =====
SESSIONS = set()

def check_login(request):
    return request.cookies.get("session") in SESSIONS

# ===== PERMISSION =====
def is_admin(uid):
    return uid == OWNER_ID or uid in ADMIN_IDS

def is_banned(uid):
    return uid in BANNED_IDS

# ===== UI MENU =====
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Dashboard", url="/admin"),
            InlineKeyboardButton(text="📅 Schedule", callback_data="menu_schedule")
        ],
        [
            InlineKeyboardButton(text="⚙️ Settings", callback_data="menu_settings"),
            InlineKeyboardButton(text="👑 Admin", callback_data="menu_admin")
        ],
        [
            InlineKeyboardButton(text="🚀 Post nhanh", callback_data="quick_post")
        ]
    ])

# ===== AI =====
async def ai_reply(text):
    try:
        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role":"system","content":"Trả lời ngắn gọn, thông minh, tiếng Việt"},
                {"role":"user","content":text}
            ]
        )
        return res.choices[0].message.content
    except Exception as e:
        print("AI ERROR:", e)
        return "⚠️ AI lỗi"

# ===== QUEUE =====
SEND_QUEUE = asyncio.Queue()

async def sender():
    while True:
        func, args = await SEND_QUEUE.get()
        try:
            await func(*args)
            await asyncio.sleep(0.8)
        except Exception as e:
            print("SEND ERROR:", e)
        SEND_QUEUE.task_done()

# ===== BUTTON =====
def build_buttons(text):
    if not text: return None
    rows, row = [], []
    for line in text.split("\n"):
        if "|" not in line: continue
        try:
            name,url=line.split("|",1)
            if not url.startswith("http"): continue
            row.append(InlineKeyboardButton(text=name.strip(),url=url.strip()))
            if len(row)==2:
                rows.append(row); row=[]
        except: continue
    if row: rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

# ===== SCHEDULER =====
async def scheduler():
    while True:
        now = get_now().strftime("%H:%M")

        for job in schedules[:]:
            try:
                if job["time"] == now and job.get("last_run") != now:

                    markup = build_buttons(job.get("button"))

                    if job.get("image"):
                        await SEND_QUEUE.put((bot.send_photo,(job["chat_id"], job["image"], job["text"], markup)))
                    else:
                        await SEND_QUEUE.put((bot.send_message,(job["chat_id"], job["text"], markup)))

                    job["last_run"] = now

                    if not job.get("repeat"):
                        schedules.remove(job)

            except Exception as e:
                print("SCHEDULE ERROR:", e)

        save_schedule(schedules)
        await asyncio.sleep(15)

# ===== BOT =====
@dp.message(Command("start"))
async def start(m: types.Message):
    if not is_admin(m.from_user.id): return
    await m.answer("🚀 BOT READY", reply_markup=main_menu())

@dp.callback_query()
async def callbacks(c: types.CallbackQuery):
    await c.message.edit_text("📋 MENU", reply_markup=main_menu())
    await c.answer()

@dp.message()
async def handle(m: types.Message):
    uid = m.from_user.id
    if is_banned(uid) or not is_admin(uid): return

    text = (m.text or "").strip().lower()

    if text in keywords:
        d = keywords[text]
        markup = build_buttons(d.get("button"))

        if d.get("image"):
            await SEND_QUEUE.put((m.answer_photo,(d["image"], d["text"], markup)))
        else:
            await SEND_QUEUE.put((m.answer,(d["text"], markup)))
    else:
        reply = await ai_reply(text)
        await SEND_QUEUE.put((m.answer,(reply, main_menu())))

# ===== WEB =====
async def login_page(request):
    return web.Response(text="LOGIN", content_type="text/html")

async def admin_page(request):
    html = f"""
    <html><body style='background:#0f172a;color:white;font-family:sans-serif'>
    <h1>🚀 SaaS Dashboard</h1>
    <p>Admin: {len(ADMIN_IDS)}</p>
    <p>Banned: {len(BANNED_IDS)}</p>
    <p>Schedule: {len(schedules)}</p>
    </body></html>
    """
    return web.Response(text=html, content_type="text/html")

async def api_stats(request):
    return web.json_response({
        "admins": len(ADMIN_IDS),
        "banned": len(BANNED_IDS),
        "schedules": len(schedules)
    })

app = web.Application()
app.router.add_get("/", lambda r: web.Response(text="BOT OK"))
app.router.add_get("/login", login_page)
app.router.add_get("/admin", admin_page)
app.router.add_get("/api/stats", api_stats)

# ===== START =====
async def start_bot(app):

    async def run_bot():
        while True:
            try:
                print("🚀 Bot running...")
                await dp.start_polling(bot)
            except Exception as e:
                print("BOT ERROR:", e)
                await asyncio.sleep(5)

    asyncio.create_task(run_bot())
    asyncio.create_task(scheduler())
    asyncio.create_task(sender())

app.on_startup.append(start_bot)

if __name__=="__main__":
    web.run_app(app,host="0.0.0.0",port=int(os.getenv("PORT",8080)))
