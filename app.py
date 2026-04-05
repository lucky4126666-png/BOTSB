import os, json, asyncio
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, select, delete
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

# ===== STATE =====
user_state = {}
temp = {}

def reset(uid):
    user_state.pop(uid, None)
    temp.pop(uid, None)

# ===== BUTTON PARSER =====
def parse_buttons(text):
    if not text:
        return None
    rows = []
    for line in text.split("\n"):
        row = []
        for part in line.split("&&"):
            if "-" in part:
                t, u = part.split("-", 1)
                row.append({"text": t.strip(), "url": u.strip()})
        if row:
            rows.append(row)
    return rows

def build_buttons(data):
    if not data:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b["text"], url=b["url"]) for b in row]
        for row in data
    ])

# ===== MODELS =====
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
    interval = Column(Integer, default=10)
    is_active = Column(Integer, default=0)
    pin = Column(Integer, default=0)

class Welcome(Base):
    __tablename__ = "welcome"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String)
    text = Column(Text)
    button = Column(Text)

# ===== MENU =====
def home():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("📌 Từ khoá", callback_data="kw_menu")],
        [InlineKeyboardButton("📅 Auto Post", callback_data="auto_menu")],
        [InlineKeyboardButton("👋 Welcome", callback_data="wel_menu")]
    ])

# ===== START =====
@dp.message(F.text == "/start")
async def start(m):
    await m.answer("🚀 Menu", reply_markup=home())

# ======================
# 📌 KEYWORD
# ======================

@dp.callback_query(F.data == "kw_menu")
async def kw_menu(c):
    await c.message.edit_text("📌 Từ khoá", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("➕ Thêm", callback_data="kw_add")],
        [InlineKeyboardButton("📋 Danh sách", callback_data="kw_list")],
        [InlineKeyboardButton("🔙 Menu", callback_data="home")]
    ]))

@dp.callback_query(F.data == "kw_add")
async def kw_add(c):
    user_state[c.from_user.id] = "kw_key"
    temp[c.from_user.id] = {}
    await c.message.edit_text("Nhập từ khoá")

@dp.callback_query(F.data == "kw_list")
async def kw_list(c):
    async with SessionLocal() as db:
        kws = (await db.execute(select(Keyword))).scalars().all()

    kb = []
    for k in kws:
        kb.append([
            InlineKeyboardButton(k.key, callback_data=f"kw_view_{k.id}"),
            InlineKeyboardButton("❌", callback_data=f"kw_del_{k.id}")
        ])

    kb.append([InlineKeyboardButton("🔙", callback_data="home")])
    await c.message.edit_text("Danh sách", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("kw_del_"))
async def kw_del(c):
    kid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        await db.execute(delete(Keyword).where(Keyword.id == kid))
        await db.commit()
    await c.answer("Đã xoá")

# ======================
# 📅 AUTO POST
# ======================

@dp.callback_query(F.data == "auto_menu")
async def auto_menu(c):
    await c.message.edit_text("📅 Auto", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("➕ Tạo", callback_data="auto_add")],
        [InlineKeyboardButton("📋 Danh sách", callback_data="auto_list")],
        [InlineKeyboardButton("🔙", callback_data="home")]
    ]))

@dp.callback_query(F.data == "auto_list")
async def auto_list(c):
    async with SessionLocal() as db:
        posts = (await db.execute(select(AutoPost))).scalars().all()

    kb = []
    for p in posts:
        kb.append([
            InlineKeyboardButton(f"Post {p.id}", callback_data=f"auto_view_{p.id}"),
            InlineKeyboardButton("❌", callback_data=f"auto_del_{p.id}")
        ])

    await c.message.edit_text("Danh sách auto", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("auto_del_"))
async def auto_del(c):
    pid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        await db.execute(delete(AutoPost).where(AutoPost.id == pid))
        await db.commit()
    await c.answer("Đã xoá")

# ======================
# 👋 WELCOME
# ======================

@dp.callback_query(F.data == "wel_menu")
async def wel_menu(c):
    await c.message.edit_text("👋 Welcome", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("✏️ Sửa text", callback_data="wel_text")],
        [InlineKeyboardButton("🔘 Sửa nút", callback_data="wel_btn")],
        [InlineKeyboardButton("👁 Preview", callback_data="wel_preview")],
        [InlineKeyboardButton("🔙", callback_data="home")]
    ]))

# ======================
# 🤖 AUTO WORKER
# ======================

async def auto_worker():
    while True:
        async with SessionLocal() as db:
            posts = (await db.execute(select(AutoPost))).scalars().all()

        for p in posts:
            if p.is_active:
                btn = build_buttons(parse_buttons(p.button))
                if p.image:
                    msg = await bot.send_photo(p.chat_id, p.image, caption=p.text or "", reply_markup=btn)
                else:
                    msg = await bot.send_message(p.chat_id, p.text or "", reply_markup=btn)

                if p.pin:
                    try:
                        await bot.pin_chat_message(p.chat_id, msg.message_id)
                    except:
                        pass

        await asyncio.sleep(60)

# ======================
# 🌐 WEBHOOK
# ======================

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    await dp.feed_update(bot, types.Update(**data))
    return {"ok": True}

@app.get("/")
async def root():
    return RedirectResponse("/dashboard")

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    asyncio.create_task(auto_worker())

    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(f"{BASE_URL}/webhook")

    print("BOT READY")
