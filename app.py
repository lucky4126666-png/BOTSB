import os, json, asyncio
from datetime import datetime, timedelta, timezone
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.types import Update
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import OpenAI

# ===== LOCK (ANTI DUPLICATE BOT) =====
LOCK_FILE = "/tmp/bot.lock"
if os.path.exists(LOCK_FILE):
    print("⚠️ Bot already running → exit")
    exit()
open(LOCK_FILE, "w").close()

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("BASE_URL")  # https://abc.up.railway.app
OWNER_ID = int(os.getenv("OWNER_ID", "123456789"))

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

# ===== PERMISSION =====
def is_admin(uid):
    return uid == OWNER_ID or uid in ADMIN_IDS

def is_banned(uid):
    return uid in BANNED_IDS

# ===== UI =====
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Dashboard", url="/admin")],
        [InlineKeyboardButton(text="🚀 Post nhanh", callback_data="quick_post")]
    ])

# ===== QUEUE =====
QUEUE = asyncio.Queue(maxsize=1000)

async def sender():
    while True:
        func, args = await QUEUE.get()
        try:
            await func(*args)
        except Exception as e:
            print("SEND ERROR:", e)
        await asyncio.sleep(0.3)
        QUEUE.task_done()

# ===== SAFE TASK =====
async def safe_task(coro, name):
    while True:
        try:
            print(f"🚀 Start {name}")
            await coro()
        except Exception as e:
            print(f"❌ {name} crash:", e)
            await asyncio.sleep(3)

# ===== AI =====
async def ai_reply(text):
    for _ in range(3):
        try:
            res = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role":"system","content":"Trả lời ngắn gọn, thông minh, tiếng Việt"},
                    {"role":"user","content":text}
                ],
                timeout=8
            )
            return res.choices[0].message.content
        except Exception as e:
            print("AI ERROR:", e)
            await asyncio.sleep(1)
    return "⚠️ AI đang bận"

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
                        await QUEUE.put((bot.send_photo,(job["chat_id"], job["image"], job["text"], markup)))
                    else:
                        await QUEUE.put((bot.send_message,(job["chat_id"], job["text"], markup)))

                    job["last_run"] = now

                    if not job.get("repeat"):
                        schedules.remove(job)

            except Exception as e:
                print("SCHEDULE ERROR:", e)

        save_schedule(schedules)
        await asyncio.sleep(15)

# ===== BOT HANDLER =====
@dp.message(Command("start"))
async def start(m: types.Message):
    if not is_admin(m.from_user.id): return
    await m.answer("🚀 BOT READY", reply_markup=main_menu())

@dp.message()
async def handle(m: types.Message):
    uid = m.from_user.id
    if is_banned(uid) or not is_admin(uid): return

    text = (m.text or "").strip().lower()

    asyncio.create_task(process_message(m, text))

async def process_message(m, text):
    try:
        if text in keywords:
            d = keywords[text]
            markup = build_buttons(d.get("button"))

            if d.get("image"):
                await QUEUE.put((m.answer_photo,(d["image"], d["text"], markup)))
            else:
                await QUEUE.put((m.answer,(d["text"], markup)))
        else:
            reply = await ai_reply(text)
            await QUEUE.put((m.answer,(reply, main_menu())))
    except Exception as e:
        print("PROCESS ERROR:", e)

# ===== WEBHOOK =====
async def webhook(request):
    try:
        data = await request.json()
        update = Update(**data)
        asyncio.create_task(dp.feed_update(bot, update))
    except Exception as e:
        print("WEBHOOK ERROR:", e)
    return web.Response(text="ok")

# ===== DASHBOARD =====
async def admin(request):
    return web.Response(text=f"""
    <html><body style='background:#0f172a;color:white;font-family:sans-serif'>
    <h1>🚀 BOT DASHBOARD</h1>
    <p>Admins: {len(ADMIN_IDS)}</p>
    <p>Banned: {len(BANNED_IDS)}</p>
    <p>Schedule: {len(schedules)}</p>
    </body></html>
    """, content_type="text/html")

async def stats(request):
    return web.json_response({
        "admins": len(ADMIN_IDS),
        "banned": len(BANNED_IDS),
        "schedules": len(schedules)
    })

# ===== START =====
async def on_start(app):
    print("🚀 Starting bot...")

    await bot.set_webhook(f"{BASE_URL}/webhook")

    asyncio.create_task(safe_task(sender, "Sender"))
    asyncio.create_task(safe_task(scheduler, "Scheduler"))

# ===== APP =====
app = web.Application()
app.router.add_post("/webhook", webhook)
app.router.add_get("/", lambda r: web.Response(text="OK"))
app.router.add_get("/admin", admin)
app.router.add_get("/api/stats", stats)

app.on_startup.append(on_start)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
