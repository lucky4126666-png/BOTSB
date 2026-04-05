import os, json, re
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from aiogram import Bot, Dispatcher, types
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, select
from openai import AsyncOpenAI
import redis.asyncio as redis

# ENV
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REDIS_URL = os.getenv("REDIS_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, class_=AsyncSession)
Base = declarative_base()

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Redis safe
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
except:
    redis_client = None

# ===== MODEL =====
class GroupConfig(Base):
    __tablename__ = "group_config"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, unique=True)
    welcome_text = Column(Text)
    welcome_image = Column(Text)
    welcome_button = Column(Text)

# ===== BUTTON =====
def build_buttons(data):
    if not data:
        return None

    if isinstance(data, str):
        data = json.loads(data)

    rows, row = [], []

    for btn in data:
        b = InlineKeyboardButton(text=btn["text"], url=btn["url"])
        row.append(b)

        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)

# ===== CONFIG =====
async def get_cfg(chat_id):
    async with SessionLocal() as db:
        result = await db.execute(select(GroupConfig).where(GroupConfig.chat_id == str(chat_id)))
        return result.scalar()

# ===== AI =====
async def ai_reply(text):
    if redis_client:
        cache = await redis_client.get(text)
        if cache:
            return cache

    resp = await client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role":"user","content":text}]
    )

    reply = resp.choices[0].message.content

    if redis_client:
        await redis_client.setex(text, 86400, reply)

    return reply

# ===== BOT =====
user_warn = {}

@dp.message()
async def handle(m: types.Message):
    text = (m.text or "").lower()

    # LOCK
    if "下课" in text:
        await bot.set_chat_permissions(m.chat.id, types.ChatPermissions(can_send_messages=False))
        await m.answer("🔒 已关闭发言")
        return

    # UNLOCK
    if "上课" in text:
        await bot.set_chat_permissions(m.chat.id, types.ChatPermissions(can_send_messages=True))
        await m.answer("🔓 已开启发言")
        return

    # PIN
    if "ghim mes" in text and m.reply_to_message:
        await bot.pin_chat_message(m.chat.id, m.reply_to_message.message_id)
        return

    # CLEAN
    if re.search(r"(http|t\.me|@)", text):
        try:
            await m.delete()
        except:
            pass
        return

    # BAD WORD
    if "scam" in text:
        uid = m.from_user.id
        user_warn[uid] = user_warn.get(uid, 0) + 1
        await m.delete()

        if user_warn[uid] >= 3:
            await bot.ban_chat_member(m.chat.id, uid)
            user_warn[uid] = 0
        return

    # WELCOME
    if m.new_chat_members:
        cfg = await get_cfg(m.chat.id)
        for u in m.new_chat_members:
            txt = (cfg.welcome_text or "👋 欢迎 {name}").replace("{name}", u.full_name)
            if cfg and cfg.welcome_image:
                await m.answer_photo(cfg.welcome_image, caption=txt, reply_markup=build_buttons(cfg.welcome_button))
            else:
                await m.answer(txt, reply_markup=build_buttons(cfg.welcome_button))
        return

    # AI
    reply = await ai_reply(text)
    await m.answer(reply)

# ===== DASHBOARD =====
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js"></script>
<style>
body{background:#0f172a;color:white;padding:20px;font-family:sans-serif}
input,textarea{width:100%;padding:10px;margin-top:10px;border-radius:10px}
button{padding:10px;margin-top:10px;border-radius:10px}
#list div{background:#1e293b;padding:10px;margin:5px;border-radius:10px;cursor:grab}
.preview{margin-top:20px;background:#111;padding:15px;border-radius:12px}
.btn{display:inline-block;background:#334155;padding:8px;margin:5px;border-radius:8px}
</style>
</head>
<body>

<h2>📘 Hướng dẫn</h2>
<p>
- **text** = in đậm<br>
- {name} = tên user<br>
- kéo thả button để đổi vị trí
</p>

<h2>🎨 Editor</h2>

<input id="chat_id" placeholder="Chat ID">
<textarea id="text" placeholder="Text"></textarea>
<input id="image" placeholder="Image URL">

<input id="btext" placeholder="Button text">
<input id="burl" placeholder="Button link">
<button onclick="add()">➕ Add</button>

<div id="list"></div>

<button onclick="save()">💾 Save</button>
<button onclick="preview()">👁 Preview</button>

<div id="preview" class="preview"></div>

<script>
let buttons=[]

function render(){
 let html=""
 buttons.forEach((b,i)=>{
   html+=`<div>${b.text} <button onclick="del(${i})">❌</button></div>`
 })
 document.getElementById("list").innerHTML=html
}

function add(){
 buttons.push({
   text:document.getElementById("btext").value,
   url:document.getElementById("burl").value
 })
 render()
}

function del(i){
 buttons.splice(i,1)
 render()
}

function preview(){
 let html=""
 buttons.forEach(b=>{
   html+=`<div class="btn">${b.text}</div>`
 })
 document.getElementById("preview").innerHTML=html
}

async function save(){
 const id=document.getElementById("chat_id").value

 await fetch(`/api/config/${id}`,{
   method:"POST",
   headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
     text:document.getElementById("text").value,
     image:document.getElementById("image").value,
     buttons:JSON.stringify(buttons)
   })
 })

 alert("Saved")
}

new Sortable(document.getElementById("list"),{
 animation:150,
 onEnd:(e)=>{
   const item=buttons.splice(e.oldIndex,1)[0]
   buttons.splice(e.newIndex,0,item)
 }
})
</script>

</body>
</html>
"""

# API
@app.post("/api/config/{chat_id}")
async def save(chat_id:str,data:dict):
    async with SessionLocal() as db:
        result = await db.execute(select(GroupConfig).where(GroupConfig.chat_id == chat_id))
        cfg = result.scalar() or GroupConfig(chat_id=chat_id)

        cfg.welcome_text = data.get("text")
        cfg.welcome_image = data.get("image")
        cfg.welcome_button = data.get("buttons")

        db.add(cfg)
        await db.commit()

    return {"ok":True}
    
    # ===== HOME PAGE =====
from fastapi.responses import RedirectResponse

@app.get("/")
async def home():
    return RedirectResponse(url="/dashboard")
    
# WEBHOOK
@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    await dp.feed_update(bot, Update(**data))
    return {"ok": True}

@app.on_event("startup")
async def startup():
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(f"{BASE_URL}/webhook")
