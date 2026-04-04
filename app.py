import os, json, asyncio, uuid
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "123456789"))
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", OWNER_ID))

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "123456")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== FILE =====
DATA_FILE = "data.json"
ADMIN_FILE = "admins.json"
BANNED_FILE = "banned.json"
SCHEDULE_FILE = "schedule.json"

# ===== LOAD SAVE =====
def load_json(file):
    try:
        if not os.path.exists(file): return {}
        with open(file) as f: return json.loads(f.read() or "{}")
    except: return {}

def save_json(file, data):
    try:
        with open(file,"w") as f: json.dump(data,f)
    except: pass

def load_list(file):
    try:
        if not os.path.exists(file): return set()
        with open(file) as f: return set(json.load(f))
    except: return set()

def save_list(file,data):
    try:
        with open(file,"w") as f: json.dump(list(data),f)
    except: pass

def load_schedule():
    try:
        if not os.path.exists(SCHEDULE_FILE): return []
        with open(SCHEDULE_FILE) as f: return json.load(f)
    except: return []

def save_schedule(data):
    try:
        with open(SCHEDULE_FILE,"w") as f: json.dump(data,f)
    except: pass

keywords = load_json(DATA_FILE)
ADMIN_IDS = load_list(ADMIN_FILE)
BANNED_IDS = load_list(BANNED_FILE)
schedules = load_schedule()

# ===== LOGIN SESSION =====
SESSIONS = set()

def check_login(request):
    return request.cookies.get("session") in SESSIONS

# ===== TIME =====
def get_now():
    return datetime.utcnow() + timedelta(hours=7)

# ===== PERMISSION =====
def is_admin(uid):
    return uid == OWNER_ID or uid in ADMIN_IDS

def is_banned(uid):
    return uid in BANNED_IDS

# ===== BUTTON =====
def build_buttons(text):
    if not text: return None
    rows, row = [], []
    for line in text.split("\n"):
        if "|" not in line: continue
        try:
            name,url=line.split("|",1)
            name,url=name.strip(),url.strip()
            if not url.startswith("http"): continue
            row.append(InlineKeyboardButton(text=name,url=url))
            if len(row)==2:
                rows.append(row); row=[]
        except: continue
    if row: rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

# ===== SCHEDULER =====
async def scheduler():
    while True:
        now = get_now().strftime("%H:%M")

        for job in schedules:
            try:
                if job["time"] == now and job.get("last_run") != now:

                    markup = build_buttons(job.get("button"))

                    if job.get("image"):
                        await bot.send_photo(job["chat_id"], job["image"], caption=job["text"], reply_markup=markup)
                    else:
                        await bot.send_message(job["chat_id"], job["text"], reply_markup=markup)

                    job["last_run"] = now

                    if not job.get("repeat"):
                        schedules.remove(job)

            except Exception as e:
                print("SCHEDULE ERROR:", e)

        save_schedule(schedules)
        await asyncio.sleep(20)

# ===== BOT =====
@dp.message(Command("start"))
async def start(m: types.Message):
    if not is_admin(m.from_user.id): return
    await m.answer("🚀 BOT READY")

@dp.message()
async def handle(m: types.Message):
    uid = m.from_user.id
    if is_banned(uid) or not is_admin(uid): return

    text = (m.text or "").strip().lower()

    if text in keywords:
        d = keywords[text]
        markup = build_buttons(d.get("button"))
        if d.get("image"):
            await m.answer_photo(d["image"], caption=d["text"], reply_markup=markup)
        else:
            await m.answer(d["text"], reply_markup=markup)

# ===== LOGIN =====
async def login_page(request):
    return web.Response(text="""
    <html><body style="background:#0f172a;color:white;text-align:center;padding-top:100px">
    <h2>🔐 LOGIN</h2>
    <form method="post">
    <input name="user"><br><br>
    <input name="pass" type="password"><br><br>
    <button>Login</button>
    </form></body></html>
    """, content_type="text/html")

async def login_post(request):
    data = await request.post()
    if data.get("user")==ADMIN_USER and data.get("pass")==ADMIN_PASS:
        sid=str(uuid.uuid4())
        SESSIONS.add(sid)
        resp=web.HTTPFound("/admin")
        resp.set_cookie("session",sid)
        return resp
    return web.Response(text="❌ Sai")

async def logout(request):
    sid=request.cookies.get("session")
    SESSIONS.discard(sid)
    resp=web.HTTPFound("/login")
    resp.del_cookie("session")
    return resp

# ===== DASHBOARD =====
async def admin_page(request):
    if not check_login(request):
        raise web.HTTPFound("/login")

    html=f"""
    <html><body style="background:#0f172a;color:white;padding:20px;font-family:sans-serif">

    <h1>🚀 CONTROL PANEL</h1>
    <a href="/logout">🚪 Logout</a>

    <h3>👑 OWNER</h3><p>{OWNER_ID}</p>

    <h3>🛡 ADMIN ({len(ADMIN_IDS)})</h3>
    {''.join([f"<p>{a} <a href='/del_admin?id={a}'>❌</a></p>" for a in ADMIN_IDS])}
    <form action="/add_admin"><input name="id"><button>Thêm</button></form>

    <h3>🚫 BANNED ({len(BANNED_IDS)})</h3>
    {''.join([f"<p>{b} <a href='/unban?id={b}'>✅</a></p>" for b in BANNED_IDS])}
    <form action="/ban"><input name="id"><button>Ban</button></form>

    <hr>

    <h2>📅 SCHEDULE ({len(schedules)})</h2>
    {''.join([f"<p>{s['time']} | {s['text'][:30]} {'🔁' if s.get('repeat') else ''} <a href='/del_schedule?id={i}'>❌</a></p>" for i,s in enumerate(schedules)])}

    <form action="/add_schedule">
    <input name="time" placeholder="HH:MM"><br><br>
    <input name="text" placeholder="Nội dung"><br><br>
    <input name="image" placeholder="file_id"><br><br>
    <textarea name="button"></textarea><br><br>
    <label>Lặp</label><input type="checkbox" name="repeat"><br><br>
    <button>Tạo</button></form>

    </body></html>
    """
    return web.Response(text=html, content_type="text/html")

# ===== ACTION =====
async def add_admin(r):
    uid=int(r.query.get("id"))
    if uid!=OWNER_ID:
        ADMIN_IDS.add(uid); save_list(ADMIN_FILE,ADMIN_IDS)
    raise web.HTTPFound("/admin")

async def del_admin(r):
    uid=int(r.query.get("id"))
    ADMIN_IDS.discard(uid); save_list(ADMIN_FILE,ADMIN_IDS)
    raise web.HTTPFound("/admin")

async def ban_user(r):
    uid=int(r.query.get("id"))
    if uid!=OWNER_ID:
        BANNED_IDS.add(uid); save_list(BANNED_FILE,BANNED_IDS)
    raise web.HTTPFound("/admin")

async def unban(r):
    uid=int(r.query.get("id"))
    BANNED_IDS.discard(uid); save_list(BANNED_FILE,BANNED_IDS)
    raise web.HTTPFound("/admin")

async def add_schedule(r):
    t=r.query.get("time")
    if not t or ":" not in t: return web.Response(text="❌ Sai giờ")
    schedules.append({
        "time":t,
        "text":r.query.get("text"),
        "image":r.query.get("image") or None,
        "button":r.query.get("button") or None,
        "chat_id":TARGET_CHAT_ID,
        "repeat":True if r.query.get("repeat") else False
    })
    save_schedule(schedules)
    raise web.HTTPFound("/admin")

async def del_schedule(r):
    i=int(r.query.get("id"))
    if i<len(schedules): schedules.pop(i)
    save_schedule(schedules)
    raise web.HTTPFound("/admin")

# ===== WEB =====
app=web.Application()
app.router.add_get("/", lambda r: web.Response(text="BOT OK"))
app.router.add_get("/login", login_page)
app.router.add_post("/login", login_post)
app.router.add_get("/logout", logout)
app.router.add_get("/admin", admin_page)

app.router.add_get("/add_admin", add_admin)
app.router.add_get("/del_admin", del_admin)
app.router.add_get("/ban", ban_user)
app.router.add_get("/unban", unban)

app.router.add_get("/add_schedule", add_schedule)
app.router.add_get("/del_schedule", del_schedule)

async def start_bot(app):
    asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(scheduler())

app.on_startup.append(start_bot)

if __name__=="__main__":
    web.run_app(app,host="0.0.0.0",port=int(os.getenv("PORT",8080)))
