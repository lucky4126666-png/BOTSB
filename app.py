import os, json, re
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, select, text as sql_text
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

# ===== ENV =====
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

# ===== ADMIN =====
ADMINS = [123456789]

def is_admin(uid):
    return uid in ADMINS

# ===== MODELS =====
class GroupConfig(Base):
    __tablename__ = "group_config"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, unique=True)
    welcome_text = Column(Text)
    welcome_button = Column(Text)
    auto_ai = Column(Integer, default=1)

class Keyword(Base):
    __tablename__ = "keywords"
    id = Column(Integer, primary_key=True)
    key = Column(String)
    text = Column(Text)
    image = Column(Text)
    button = Column(Text)

class AutoPost(Base):
    __tablename__ = "auto_post"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String)
    text = Column(Text)
    image = Column(Text)
    button = Column(Text)
    interval = Column(Integer)

# ===== STATE =====
user_state = {}
temp = {}

def reset(uid):
    user_state.pop(uid, None)
    temp.pop(uid, None)

# ===== BUTTON =====
def smart_buttons(data=None, extra=None):
    rows = []

    if data:
        try:
            if isinstance(data, str):
                data = json.loads(data)
        except:
            data = []

        row = []
        for b in data:
            row.append(InlineKeyboardButton(text=b["text"], url=b["url"]))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

    if extra:
        rows.extend(extra)

    rows.append([
        InlineKeyboardButton(text="🏠 Menu", callback_data="home"),
        InlineKeyboardButton(text="📖 Hướng dẫn", callback_data="guide")
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

# ===== SEND =====
async def send_msg(m, text="", image=None, button=None):
    markup = smart_buttons(button)

    if image:
        await m.answer_photo(photo=image, caption=text or "‎", reply_markup=markup)
    else:
        await m.answer(text or "‎", reply_markup=markup)

# ===== MENU =====
def home_menu():
    return smart_buttons(extra=[
        [InlineKeyboardButton(text="📌 Từ khoá", callback_data="kw_menu")],
        [InlineKeyboardButton(text="👋 Lời chào", callback_data="welcome_menu")],
        [InlineKeyboardButton(text="📅 Auto Post", callback_data="post_menu")]
    ])

# ===== START =====
@dp.message(F.text == "/start")
async def start(m):
    await m.answer("🚀 Control Panel", reply_markup=home_menu())

# ===== CALLBACK =====
@dp.callback_query()
async def cb(c):
    d = c.data
    uid = c.from_user.id

    if d == "home":
        await c.message.edit_text("🏠 Menu", reply_markup=home_menu())

    elif d == "guide":
        await c.message.edit_text("📖 Hướng dẫn:\nBấm nút để sử dụng bot", reply_markup=home_menu())

    elif d == "kw_menu":
        await c.message.edit_text("📌 Từ khoá", reply_markup=smart_buttons(extra=[
            [InlineKeyboardButton(text="➕ Thêm", callback_data="kw_add")],
            [InlineKeyboardButton(text="📋 Danh sách", callback_data="kw_list")]
        ]))

    elif d == "kw_add":
        user_state[uid] = "kw_key"
        await c.message.edit_text("👉 Nhập từ khoá")

    elif d == "kw_save":
        data = temp[uid]
        async with SessionLocal() as db:
            db.add(Keyword(**data))
            await db.commit()
        reset(uid)
        await c.message.edit_text("✅ Đã lưu", reply_markup=home_menu())

    elif d == "kw_list":
        async with SessionLocal() as db:
            res = await db.execute(select(Keyword))
            kws = res.scalars().all()
        txt = "\n".join([k.key for k in kws]) or "Chưa có"
        await c.message.edit_text(txt, reply_markup=home_menu())

    elif d == "welcome_menu":
        user_state[uid] = "welcome"
        await c.message.edit_text("👉 Nhập nội dung welcome")

    elif d == "post_menu":
        await c.message.edit_text("📅 Auto Post", reply_markup=smart_buttons(extra=[
            [InlineKeyboardButton(text="➕ Tạo", callback_data="post_add")]
        ]))

    elif d == "post_add":
        user_state[uid] = "post_text"
        await c.message.edit_text("👉 Nhập nội dung post")

    elif d == "post_save":
        data = temp[uid]

        async with SessionLocal() as db:
            db.add(AutoPost(**data))
            await db.commit()

        scheduler.add_job(send_post, "interval", minutes=data["interval"],
                          args=[data["chat_id"], data["text"], data["image"], data["button"]])

        reset(uid)
        await c.message.edit_text("✅ Đã lưu auto post", reply_markup=home_menu())

    elif d.startswith("time_"):
        temp[uid]["interval"] = int(d.split("_")[1])

        data = temp[uid]

        await send_msg(c.message, data["text"], data.get("image"), data.get("button"))

        await c.message.answer("👁 Preview", reply_markup=smart_buttons(extra=[
            [InlineKeyboardButton(text="💾 Lưu", callback_data="post_save")]
        ]))

    await c.answer()

# ===== INPUT =====
@dp.message()
async def handle(m):
    uid = m.from_user.id
    text = m.text or ""
    state = user_state.get(uid)

    # ===== KEYWORD =====
    async with SessionLocal() as db:
        res = await db.execute(select(Keyword))
        kws = res.scalars().all()

    for k in kws:
        if k.key.lower() in text.lower():
            await send_msg(m, k.text, k.image, k.button)
            return

    # ===== STATE =====
    if state == "kw_key":
        temp[uid] = {"key": text}
        user_state[uid] = "kw_text"
        await send_msg(m, "👉 Nhập nội dung")

    elif state == "kw_text":
        temp[uid]["text"] = text
        user_state[uid] = None
        await send_msg(m, "👁 Preview", button=None)

    elif state == "welcome":
        async with SessionLocal() as db:
            res = await db.execute(select(GroupConfig).where(GroupConfig.chat_id == str(m.chat.id)))
            cfg = res.scalar() or GroupConfig(chat_id=str(m.chat.id))
            cfg.welcome_text = text
            db.add(cfg)
            await db.commit()

        reset(uid)
        await send_msg(m, "✅ Đã lưu welcome")

    elif state == "post_text":
        temp[uid] = {
            "chat_id": str(m.chat.id),
            "text": text,
            "image": None,
            "button": None
        }

        await m.answer("⏱ Chọn thời gian", reply_markup=smart_buttons(extra=[
            [InlineKeyboardButton(text="10p", callback_data="time_10")],
            [InlineKeyboardButton(text="30p", callback_data="time_30")],
            [InlineKeyboardButton(text="60p", callback_data="time_60")]
        ]))

    # ===== AI (ADMIN ONLY) =====
    if is_admin(uid):
        await m.answer("🤖 AI đang hoạt động")

# ===== AUTO POST =====
async def send_post(chat_id, text, image=None, button=None):
    markup = smart_buttons(button)

    if image:
        await bot.send_photo(chat_id, image, caption=text, reply_markup=markup)
    else:
        await bot.send_message(chat_id, text, reply_markup=markup)

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

        await conn.execute(sql_text(
            "ALTER TABLE group_config ADD COLUMN IF NOT EXISTS auto_ai INTEGER DEFAULT 1"
        ))

    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(f"{BASE_URL}/webhook")

    print("🚀 BOT READY")
