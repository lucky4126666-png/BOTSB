import os, json
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

engine = create_async_engine(DATABASE_URL)
SessionLocal = sessionmaker(engine, class_=AsyncSession)
Base = declarative_base()

scheduler = AsyncIOScheduler()
scheduler.start()

ADMINS = [123456789]

# ===== DB =====
class AutoPost(Base):
    __tablename__ = "auto_post"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String)
    text = Column(Text)
    interval = Column(Integer)

# ===== STATE =====
user_state = {}
temp = {}

def reset(uid):
    user_state.pop(uid, None)
    temp.pop(uid, None)

# ===== UI =====
def home_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Auto Post", callback_data="post_menu")],
        [InlineKeyboardButton(text="📖 Hướng dẫn", callback_data="guide")]
    ])

def time_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="10 phút", callback_data="time_10")],
        [InlineKeyboardButton(text="30 phút", callback_data="time_30")],
        [InlineKeyboardButton(text="60 phút", callback_data="time_60")],
        [InlineKeyboardButton(text="🔙 Menu", callback_data="home")]
    ])

# ===== START =====
@dp.message(F.text == "/start")
async def start(m):
    await m.answer("🚀 Menu chính", reply_markup=home_menu())

# ===== CALLBACK =====
@dp.callback_query()
async def cb(c):
    uid = c.from_user.id
    d = c.data

    if d == "home":
        await c.message.edit_text("🏠 Menu chính", reply_markup=home_menu())

    elif d == "guide":
        await c.message.edit_text(
            "📖 Hướng dẫn:\n\n"
            "1. Tạo Auto Post\n"
            "2. Nhập nội dung\n"
            "3. Chọn thời gian\n"
            "4. Lưu\n\n"
            "👉 chỉ cần bấm nút",
            reply_markup=home_menu()
        )

    elif d == "post_menu":
        await c.message.edit_text(
            "📅 Auto Post",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Tạo", callback_data="post_add")],
                [InlineKeyboardButton(text="📋 Danh sách", callback_data="post_list")],
                [InlineKeyboardButton(text="🔙 Menu", callback_data="home")]
            ])
        )

    elif d == "post_add":
        user_state[uid] = "post_text"
        temp[uid] = {}

        await c.message.edit_text("📝 Nhập nội dung bài viết")

    elif d.startswith("time_"):
        minutes = int(d.split("_")[1])
        temp[uid]["interval"] = minutes

        data = temp[uid]

        await c.message.edit_text(
            f"👁 Preview:\n\n{data['text']}\n\n⏱ {minutes} phút",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💾 Lưu", callback_data="post_save")],
                [InlineKeyboardButton(text="❌ Huỷ", callback_data="home")]
            ])
        )

    elif d == "post_save":
        data = temp[uid]

        async with SessionLocal() as db:
            db.add(AutoPost(
                chat_id=str(c.message.chat.id),
                text=data["text"],
                interval=data["interval"]
            ))
            await db.commit()

        scheduler.add_job(
            send_post,
            "interval",
            minutes=data["interval"],
            args=[c.message.chat.id, data["text"]]
        )

        reset(uid)

        await c.message.edit_text("✅ Đã lưu auto post", reply_markup=home_menu())

    elif d == "post_list":
        async with SessionLocal() as db:
            res = await db.execute(select(AutoPost))
            posts = res.scalars().all()

        txt = "\n".join([f"{p.id}. {p.text[:20]} ({p.interval}p)" for p in posts]) or "Chưa có"

        await c.message.edit_text(
            "📋 Danh sách:\n\n" + txt,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Menu", callback_data="home")]
            ])
        )

    await c.answer()

# ===== INPUT =====
@dp.message()
async def handle(m):
    uid = m.from_user.id
    state = user_state.get(uid)

    if not state:
        return

    if state == "post_text":
        temp[uid]["text"] = m.text
        user_state[uid] = None

        await m.answer("⏱ Chọn thời gian", reply_markup=time_menu())

# ===== AUTO POST =====
async def send_post(chat_id, text):
    await bot.send_message(chat_id, text)

# ===== WEB =====
@app.get("/")
async def home():
    return RedirectResponse("/dashboard")

@app.get("/dashboard")
async def dash():
    return {"status": "ok"}

# ===== WEBHOOK =====
@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    await dp.feed_update(bot, types.Update(**data))
    return {"ok": True}

# ===== STARTUP =====
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(f"{BASE_URL}/webhook")

    print("🚀 BOT READY")
