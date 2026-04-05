import os, json, re
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from aiogram import Bot, Dispatcher, types
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, select
from openai import AsyncOpenAI
import redis.asyncio as redis

# ===== ENV =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REDIS_URL = os.getenv("REDIS_URL")

# ===== INIT =====
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

class Keyword(Base):
    __tablename__ = "keywords"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True)
    text = Column(Text)
    image = Column(Text)
    button = Column(Text)

# ===== BUTTON =====
def build_buttons(data):
    if not data:
        return None

    try:
        if isinstance(data, str):
            data = json.loads(data)
    except:
        return None

    rows, row = [], []
    for b in data:
        btn = InlineKeyboardButton(text=b["text"], url=b["url"])
        row.append(btn)

        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)

# ===== CONFIG =====
async def get_cfg(chat_id):
    async with SessionLocal() as db:
        res = await db.execute(select(GroupConfig).where(GroupConfig.chat_id == str(chat_id)))
        return res.scalar()

# ===== KEYWORD =====
async def get_keyword(text):
    async with SessionLocal() as db:
        res = await db.execute(select(Keyword).where(Keyword.key == text))
        kw = res.scalar()

    if kw:
        return {"text": kw.text, "image": kw.image, "button": kw.button}

    return None

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

# ===== EDIT MODE =====
edit_mode = {}

# ===== BOT =====
@dp.message()
async def handle(m: types.Message):
    text = (m.text or "").lower()

    # ===== SET WELCOME (BOT) =====
    if text == "set welcome":
        edit_mode[m.from_user.id] = "welcome"
        await m.answer("📩 Gửi nội dung welcome")
        return

    if m.from_user.id in edit_mode:
        mode = edit_mode[m.from_user.id]

        async with SessionLocal() as db:
            res = await db.execute(select(GroupConfig).where(GroupConfig.chat_id == str(m.chat.id)))
            cfg = res.scalar() or GroupConfig(chat_id=str(m.chat.id))

            if mode == "welcome":
                cfg.welcome_text = m.text

            db.add(cfg)
            await db.commit()

        del edit_mode[m.from_user.id]
        await m.answer("✅ Đã lưu")
        return

    # ===== LOCK =====
    if "下课" in text:
        await bot.set_chat_permissions(m.chat.id, types.ChatPermissions(can_send_messages=False))
        await m.answer("🔒 已关闭发言")
        return

    if "上课" in text:
        await bot.set_chat_permissions(m.chat.id, types.ChatPermissions(can_send_messages=True))
        await m.answer("🔓 已开启发言")
        return

    # ===== PIN =====
    if "ghim mes" in text and m.reply_to_message:
        await bot.pin_chat_message(m.chat.id, m.reply_to_message.message_id)
        return

    # ===== CLEAN =====
    if re.search(r"(http|t\.me|@)", text):
        try:
            await m.delete()
        except:
            pass
        return

    # ===== LOAD CONFIG =====
    cfg = await get_cfg(m.chat.id)

    # ===== WELCOME =====
    if m.new_chat_members:
        if cfg:
            for u in m.new_chat_members:
                txt = (cfg.welcome_text or "👋 Welcome {name}").replace("{name}", u.full_name)

                if cfg.welcome_image:
                    await m.answer_photo(cfg.welcome_image, caption=txt, reply_markup=build_buttons(cfg.welcome_button))
                else:
                    await m.answer(txt, reply_markup=build_buttons(cfg.welcome_button))
        return

    # ===== KEYWORD =====
    kw = await get_keyword(text)
    if kw:
        markup = build_buttons(kw["button"])
        if kw["image"]:
            await m.answer_photo(kw["image"], caption=kw["text"], reply_markup=markup)
        else:
            await m.answer(kw["text"], reply_markup=markup)
        return

    # ===== AI =====
    reply = await ai_reply(text)
    await m.answer(reply)

# ===== HOME =====
@app.get("/")
async def home():
    return RedirectResponse("/dashboard")

# ===== DASHBOARD =====
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return "<h2>Dashboard đang chạy 🚀</h2>"

# ===== API SAVE =====
@app.post("/api/config/{chat_id}")
async def save(chat_id:str,data:dict):
    async with SessionLocal() as db:
        res = await db.execute(select(GroupConfig).where(GroupConfig.chat_id == chat_id))
        cfg = res.scalar() or GroupConfig(chat_id=chat_id)

        cfg.welcome_text = data.get("text")
        cfg.welcome_image = data.get("image")
        cfg.welcome_button = data.get("buttons")

        db.add(cfg)
        await db.commit()

    return {"ok":True}

# ===== WEBHOOK =====
@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    await dp.feed_update(bot, Update(**data))
    return {"ok": True}

@app.on_event("startup")
async def startup():
    print("🚀 STARTING...")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(f"{BASE_URL}/webhook")
