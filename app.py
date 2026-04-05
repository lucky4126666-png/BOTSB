import os, json, re
from fastapi import FastAPI, Request
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

# INIT
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, class_=AsyncSession)
Base = declarative_base()

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Redis safe init
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
except:
    redis_client = None

# MODEL
class Keyword(Base):
    __tablename__ = "keywords"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True)
    text = Column(Text)
    image = Column(Text)
    button = Column(Text)

# BUTTON
def build_buttons(text):
    if not text:
        return None

    rows, row = [], []
    for line in text.split("\n"):
        if "|" not in line:
            continue
        name, link = line.split("|", 1)
        row.append(InlineKeyboardButton(text=name, url=link))

        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)

# CACHE
async def get_keyword(text):
    if redis_client:
        cache = await redis_client.get(f"kw:{text}")
        if cache:
            return json.loads(cache)

    async with SessionLocal() as db:
        result = await db.execute(select(Keyword).where(Keyword.key == text))
        kw = result.scalar()

    if kw:
        data = {"text": kw.text, "image": kw.image, "button": kw.button}
        if redis_client:
            await redis_client.setex(f"kw:{text}", 3600, json.dumps(data))
        return data

    return None

# AI
async def ai_reply(text):
    if redis_client:
        cache = await redis_client.get(f"ai:{text}")
        if cache:
            return cache

    resp = await client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": text}]
    )

    reply = resp.choices[0].message.content

    if redis_client:
        await redis_client.setex(f"ai:{text}", 86400, reply)

    return reply

# MAIN
@dp.message()
async def handle(m: types.Message):
    text = (m.text or "").lower()

    # LOCK
    if "下课" in text:
        await bot.set_chat_permissions(m.chat.id, types.ChatPermissions(can_send_messages=False))
        await m.answer("🔒 本群已关闭发言")
        return

    # UNLOCK
    if "上课" in text:
        await bot.set_chat_permissions(m.chat.id, types.ChatPermissions(can_send_messages=True))
        await m.answer("🔓 本群已开启发言")
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

    # KEYWORD
    kw = await get_keyword(text)
    if kw:
        markup = build_buttons(kw["button"])
        if kw["image"]:
            await m.answer_photo(kw["image"], caption=kw["text"], reply_markup=markup)
        else:
            await m.answer(kw["text"], reply_markup=markup)
        return

    # AI
    reply = await ai_reply(text)
    await m.answer(reply)

# WEBHOOK
@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.on_event("startup")
async def startup():
    print("BOT STARTING...")
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(f"{BASE_URL}/webhook")
