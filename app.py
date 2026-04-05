import os, json, re, time
from fastapi import FastAPI, Request
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

redis_client = redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_timeout=5
)

# ===== MODELS =====
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
    role = Column(String)
    content = Column(Text)

class GroupConfig(Base):
    __tablename__ = "group_config"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, unique=True)

    welcome_text = Column(Text)
    welcome_image = Column(Text)
    welcome_button = Column(Text)

    lock_text = Column(Text)
    lock_button = Column(Text)

# ===== BUTTON =====
def build_buttons(text):
    if not text:
        return None

    rows, row = [], []
    for line in text.split("\n"):
        if "|" not in line:
            continue
        name, action = line.split("|", 1)

        if action.startswith("callback:"):
            btn = InlineKeyboardButton(text=name, callback_data=action.replace("callback:", ""))
        else:
            btn = InlineKeyboardButton(text=name, url=action)

        row.append(btn)
        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)

# ===== DB HELPERS =====
async def get_memory(user_id):
    async with SessionLocal() as db:
        result = await db.execute(
            select(Memory).where(Memory.user_id == str(user_id)).order_by(Memory.id.desc()).limit(10)
        )
        rows = result.scalars().all()
        return [{"role": r.role, "content": r.content} for r in reversed(rows)]

async def save_memory(user_id, user_text, bot_text):
    async with SessionLocal() as db:
        db.add_all([
            Memory(user_id=str(user_id), role="user", content=user_text),
            Memory(user_id=str(user_id), role="assistant", content=bot_text)
        ])
        await db.commit()

async def get_group_config(chat_id):
    async with SessionLocal() as db:
        result = await db.execute(
            select(GroupConfig).where(GroupConfig.chat_id == str(chat_id))
        )
        return result.scalar()

# ===== CACHE =====
async def get_keyword_cached(text):
    cache = await redis_client.get(f"kw:{text}")
    if cache:
        return json.loads(cache)

    async with SessionLocal() as db:
        result = await db.execute(select(Keyword).where(Keyword.key == text))
        kw = result.scalar()

    if kw:
        data = {"text": kw.text, "image": kw.image, "button": kw.button}
        await redis_client.setex(f"kw:{text}", 3600, json.dumps(data))
        return data

    return None

async def is_spam(user_id):
    key = f"spam:{user_id}"
    count = await redis_client.get(key)

    if count and int(count) > 5:
        return True

    await redis_client.incr(key)
    await redis_client.expire(key, 10)
    return False

# ===== AI =====
async def ai_reply(user_id, text):
    cache = await redis_client.get(f"ai:{text}")
    if cache:
        return cache

    memory = await get_memory(user_id)

    messages = [{"role": "system", "content": "Bạn là trợ lý AI."}]
    messages += memory
    messages.append({"role": "user", "content": text})

    resp = await client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=messages
    )

    reply = resp.choices[0].message.content

    await save_memory(user_id, text, reply)
    await redis_client.setex(f"ai:{text}", 86400, reply)

    return reply

# ===== FEATURES =====
LOCK_KEY = ["đóng nhóm", "下课"]
UNLOCK_KEY = ["mở nhóm", "上课"]

user_warn = {}

async def handle_welcome(m):
    if not m.new_chat_members:
        return False

    cfg = await get_group_config(m.chat.id)

    for user in m.new_chat_members:
        text = (cfg.welcome_text if cfg else "👋 欢迎 {name}")
        text = text.replace("{name}", user.full_name)

        if cfg and cfg.welcome_image:
            await m.answer_photo(
                cfg.welcome_image,
                caption=text,
                reply_markup=build_buttons(cfg.welcome_button)
            )
        else:
            await m.answer(text, reply_markup=build_buttons(cfg.welcome_button if cfg else None))

    return True

async def handle_group_features(m):
    text = (m.text or "").lower()
    cfg = await get_group_config(m.chat.id)

    # LOCK
    if any(k in text for k in LOCK_KEY):
        await bot.set_chat_permissions(m.chat.id, types.ChatPermissions(can_send_messages=False))
        await m.answer(
            (cfg.lock_text if cfg else "🔒 已关闭发言"),
            reply_markup=build_buttons(cfg.lock_button if cfg else None)
        )
        return True

    # UNLOCK
    if any(k in text for k in UNLOCK_KEY):
        await bot.set_chat_permissions(m.chat.id, types.ChatPermissions(can_send_messages=True))
        await m.answer("🔓 已开启发言")
        return True

    # PIN
    if "ghim mes" in text and m.reply_to_message:
        await bot.pin_chat_message(m.chat.id, m.reply_to_message.message_id)
        return True

    # CLEAN
    if re.search(r"(http|t\.me|@)", text):
        try:
            await m.delete()
        except:
            pass
        return True

    # BAD WORD
    if "scam" in text or "lừa đảo" in text:
        uid = m.from_user.id
        user_warn[uid] = user_warn.get(uid, 0) + 1
        await m.delete()

        if user_warn[uid] >= 3:
            await bot.ban_chat_member(m.chat.id, uid)
            user_warn[uid] = 0

        return True

    return False

# ===== MAIN HANDLER =====
@dp.message()
async def handle(m: types.Message):

    if await is_spam(m.from_user.id):
        return

    # welcome
    if await handle_welcome(m):
        return

    # group features
    if m.chat.type in ["group", "supergroup"]:
        if await handle_group_features(m):
            return

    text = (m.text or "").strip().lower()

    keyword = await get_keyword_cached(text)

    if keyword:
        markup = build_buttons(keyword["button"])

        if keyword["image"]:
            await m.answer_photo(keyword["image"], caption=keyword["text"], reply_markup=markup)
        else:
            await m.answer(keyword["text"], reply_markup=markup)
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

@app.on_event("startup")
async def startup():
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(f"{BASE_URL}/webhook")
