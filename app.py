import os, asyncio
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, select, delete
from openai import AsyncOpenAI

# ===== ENV =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

# ===== INIT =====
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# ===== DB =====
Base = declarative_base()

class Keyword(Base):
    __tablename__ = "keywords"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True)
    text = Column(Text)
    image = Column(Text)
    button = Column(Text)

class Memory(Base):
    __tablename__ = "memory"
    id = Column(Integer, primary_key=True)
    user_id = Column(String)
    content = Column(Text)

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# ===== START =====
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(f"{BASE_URL}/webhook")

# ===== BUTTON =====
def build_buttons(text):
    if not text:
        return None
    rows, row = [], []
    for line in text.split("\n"):
        if "|" not in line:
            continue
        name, url = line.split("|", 1)
        row.append(InlineKeyboardButton(text=name.strip(), url=url.strip()))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ===== AI =====
async def ai_reply(uid, text):
    async with SessionLocal() as db:
        result = await db.execute(
            select(Memory).where(Memory.user_id == str(uid)).limit(5)
        )
        history = result.scalars().all()

        messages = [{"role": "system", "content": "Trả lời ngắn gọn, tiếng Việt"}]

        for h in history:
            messages.append({"role": "user", "content": h.content})

        messages.append({"role": "user", "content": text})

        res = await client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages
        )

        reply = res.choices[0].message.content

        db.add(Memory(user_id=str(uid), content=text))
        await db.commit()

        return reply

# ===== BOT =====
@dp.message()
async def handle(m: types.Message):
    text = (m.text or "").strip().lower()

    async with SessionLocal() as db:
        result = await db.execute(
            select(Keyword).where(Keyword.key == text)
        )
        keyword = result.scalar()

    if keyword:
        markup = build_buttons(keyword.button)

        if keyword.image:
            await m.answer_photo(keyword.image, caption=keyword.text, reply_markup=markup)
        else:
            await m.answer(keyword.text, reply_markup=markup)
    else:
        reply = await ai_reply(m.from_user.id, text)
        await m.answer(reply)

# ===== WEBHOOK =====
@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}

# ===== API =====
@app.get("/api/keywords")
async def list_keywords():
    async with SessionLocal() as db:
        result = await db.execute(select(Keyword))
        return result.scalars().all()

@app.post("/api/keywords")
async def add_keyword(data: dict):
    async with SessionLocal() as db:
        k = Keyword(**data)
        db.add(k)
        await db.commit()
    return {"ok": True}

@app.delete("/api/keywords/{key}")
async def delete_keyword(key: str):
    async with SessionLocal() as db:
        await db.execute(delete(Keyword).where(Keyword.key == key))
        await db.commit()
    return {"ok": True}
