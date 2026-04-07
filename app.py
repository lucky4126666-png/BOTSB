import os
import time
import asyncio
import random
from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.client.default import DefaultBotProperties

from sqlalchemy import Column, Integer, String, Text, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base

from openai import AsyncOpenAI

# ================== CONFIG ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data.db")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = FastAPI()

ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# ================== DATABASE ==================

Base = declarative_base()

engine = create_async_engine(DATABASE_URL)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Keyword(Base):
    __tablename__ = "keywords"

    id = Column(Integer, primary_key=True)
    key = Column(String)
    text = Column(Text)
    image = Column(String)
    button = Column(String)
    mode = Column(String, default="contains")
    active = Column(Integer, default=1)
    priority = Column(Integer, default=0)


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True)
    text = Column(Text)
    image = Column(String)
    button = Column(String)
    schedule_at = Column(Integer)
    sent = Column(Integer, default=0)


# ================== MEMORY PRO ==================

ai_memory = defaultdict(list)
ai_last_used = {}

MAX_MEMORY = 8
MEMORY_TTL = 600


def clean_memory(key):
    now = time.time()
    last = ai_last_used.get(key, 0)

    if now - last > MEMORY_TTL:
        ai_memory[key] = []


# ================== AI ==================

async def ask_ai(chat_id: str, user_id: str, user_text: str):
    if not OPENAI_API_KEY:
        return "⚠️ AI chưa cấu hình"

    key = f"{chat_id}_{user_id}"

    clean_memory(key)

    history = ai_memory[key]
    history.append({"role": "user", "content": user_text})
    history = history[-MAX_MEMORY:]

    try:
        resp = await ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": """
Bạn là bot Telegram chuyên nghiệp:
- Trả lời ngắn gọn
- Tự nhiên như người thật
- Không spam
- Không lặp lại
"""
                },
                *history
            ]
        )

        reply = resp.choices[0].message.content

        history.append({"role": "assistant", "content": reply})
        ai_memory[key] = history
        ai_last_used[key] = time.time()

        return reply

    except Exception as e:
        print("AI ERROR:", e)
        return "😢 AI đang bận"


# ================== SEND ==================

async def send_preview(chat_id, text, image=None, button=None):
    markup = None

    if button:
        try:
            label, url = button.split("|")
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=label, url=url)]]
            )
        except:
            pass

    if image:
        return await bot.send_photo(chat_id, photo=image, caption=text, reply_markup=markup)

    return await bot.send_message(chat_id, text, reply_markup=markup)


# ================== HANDLER ==================

last_ai_call = {}
AI_COOLDOWN = 5


@dp.message(CommandStart())
async def start(m: Message):
    await m.answer("🚀 Bot PRO đã sẵn sàng!")


@dp.message(F.text)
async def all_messages(m: Message):
    text_ = m.text.strip()
    lower_text = text_.lower()

    if len(text_) < 2:
        return

    # ================= KEYWORD =================
    async with SessionLocal() as db:
        kws = (await db.execute(
            select(Keyword).where(Keyword.active == 1)
        )).scalars().all()

    kws = sorted(kws, key=lambda x: x.priority or 0, reverse=True)

    matched = None

    for k in kws:
        key = (k.key or "").lower()

        if k.mode == "exact" and lower_text == key:
            matched = k
            break

        if k.mode == "contains" and key in lower_text:
            matched = k
            break

    if matched:
        replies = (matched.text or "").split("|")
        reply = random.choice(replies)

        await send_preview(
            chat_id=m.chat.id,
            text=reply,
            image=matched.image,
            button=matched.button
        )
        return

    # ================= AI =================
    now = time.time()
    last = last_ai_call.get(m.chat.id, 0)

    if now - last < AI_COOLDOWN:
        return

    last_ai_call[m.chat.id] = now

    ai_reply = await ask_ai(
        str(m.chat.id),
        str(m.from_user.id),
        text_
    )

    await send_preview(
        chat_id=m.chat.id,
        text=ai_reply
    )


# ================= RESET =================

@dp.message(F.text == "/reset")
async def reset_ai(m: Message):
    key = f"{m.chat.id}_{m.from_user.id}"
    ai_memory[key] = []
    await m.reply("🧠 Đã reset trí nhớ!")


# ================= AUTO POST =================

async def auto_post_loop():
    while True:
        try:
            async with SessionLocal() as db:
                posts = (await db.execute(
                    select(Post).where(Post.sent == 0)
                )).scalars().all()

                now = int(time.time())

                for p in posts:
                    if p.schedule_at and p.schedule_at <= now:
                        try:
                            await send_preview(
                                chat_id=os.getenv("TARGET_CHAT_ID"),
                                text=p.text,
                                image=p.image,
                                button=p.button
                            )

                            p.sent = 1
                            await db.commit()

                            print(f"[AUTO SENT] {p.id}")

                        except Exception as e:
                            print(f"[AUTO ERROR] {p.id} -> {e}")

        except Exception as e:
            print("AUTO LOOP ERROR:", e)

        await asyncio.sleep(10)


# ================= WEBHOOK =================

@app.on_event("startup")
async def on_startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    webhook_url = f"{BASE_URL}/webhook"

    await bot.set_webhook(webhook_url, drop_pending_updates=True)

    asyncio.create_task(auto_post_loop())

    print("🚀 BOT STARTED")


@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    await dp.feed_raw_update(bot, data)
    return {"ok": True}


@app.get("/health")
async def health():
    return {"ok": True}
