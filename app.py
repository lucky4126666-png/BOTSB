import os, json, re, asyncio
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, select
from openai import AsyncOpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ===== ENV =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ===== INIT =====
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

engine = create_async_engine(DATABASE_URL)
SessionLocal = sessionmaker(engine, class_=AsyncSession)
Base = declarative_base()

client = AsyncOpenAI(api_key=OPENAI_API_KEY)
scheduler = AsyncIOScheduler()
scheduler.start()

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
    button = Column(Text)

# ===== BUTTON SYSTEM =====
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

    rows.append([InlineKeyboardButton(text="🏠 Menu", callback_data="home")])

    return InlineKeyboardMarkup(inline_keyboard=rows)

# ===== MENU =====
def home_menu():
    return smart_buttons(extra=[
        [InlineKeyboardButton(text="📌 Từ khoá", callback_data="kw_menu")],
        [InlineKeyboardButton(text="👋 Lời chào", callback_data="welcome_menu")],
        [InlineKeyboardButton(text="📅 Auto Post", callback_data="post_menu")]
    ])

# ===== DB =====
async def get_cfg(chat_id):
    async with SessionLocal() as db:
        res = await db.execute(select(GroupConfig).where(GroupConfig.chat_id == str(chat_id)))
        return res.scalar()

async def get_keywords():
    async with SessionLocal() as db:
        res = await db.execute(select(Keyword))
        return res.scalars().all()

# ===== AI =====
async def ai_reply(text):
    resp = await client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Trả lời tiếng Việt ngắn gọn"},
            {"role": "user", "content": text}
        ]
    )
    return resp.choices[0].message.content

# ===== STATE =====
user_state = {}
temp = {}

# ===== START =====
@dp.message(F.text == "/start")
async def start(m):
    await m.answer("🚀 Control Panel", reply_markup=home_menu())

# ===== CALLBACK =====
@dp.callback_query()
async def cb(c):
    d = c.data
    uid = c.from_user.id

    # HOME
    if d == "home":
        await c.message.edit_text("🏠 Menu", reply_markup=home_menu())

    # ===== KEYWORD =====
    elif d == "kw_menu":
        await c.message.edit_text(
            "📌 Từ khoá",
            reply_markup=smart_buttons(extra=[
                [InlineKeyboardButton(text="➕ Thêm", callback_data="kw_add")],
                [InlineKeyboardButton(text="📋 Danh sách", callback_data="kw_list")]
            ])
        )

    elif d == "kw_add":
        user_state[uid] = "kw_key"
        temp[uid] = {}
        await c.message.edit_text("👉 Nhập từ khoá")

    elif d == "kw_save":
        data = temp.get(uid)

        async with SessionLocal() as db:
            db.add(Keyword(**data))
            await db.commit()

        user_state.pop(uid, None)
        await c.message.edit_text("✅ Đã lưu", reply_markup=home_menu())

    elif d == "kw_list":
        kws = await get_keywords()
        txt = "\n".join([f"- {k.key}" for k in kws]) or "Chưa có"
        await c.message.edit_text(txt, reply_markup=home_menu())

    # ===== WELCOME =====
    elif d == "welcome_menu":
        user_state[uid] = "welcome"
        await c.message.edit_text("👉 Nhập nội dung welcome")

    # ===== AUTO POST =====
    elif d == "post_menu":
        await c.message.edit_text(
            "📅 Auto Post",
            reply_markup=smart_buttons(extra=[
                [InlineKeyboardButton(text="➕ Tạo", callback_data="post_add")]
            ])
        )

    elif d == "post_add":
        user_state[uid] = "post_text"
        await c.message.edit_text("👉 Nhập nội dung post")

    elif d == "post_save":
        data = temp.get(uid)

        scheduler.add_job(
            send_post,
            "interval",
            minutes=1,
            args=[c.message.chat.id, data["text"], data.get("button")]
        )

        await c.message.edit_text("✅ Đã tạo auto post", reply_markup=home_menu())

    await c.answer()

# ===== INPUT =====
@dp.message()
async def input_handler(m):
    uid = m.from_user.id
    state = user_state.get(uid)
    text = m.text or ""

    if state == "kw_key":
        temp[uid] = {"key": text}
        user_state[uid] = "kw_text"
        await m.answer("👉 Nhập nội dung")

    elif state == "kw_text":
        temp[uid]["text"] = text
        user_state[uid] = "kw_button"
        await m.answer("👉 Nhập button JSON hoặc skip")

    elif state == "kw_button":
        temp[uid]["button"] = text
        user_state[uid] = None

        await m.answer(
            "👁 Preview",
            reply_markup=smart_buttons(extra=[
                [InlineKeyboardButton(text="💾 Lưu", callback_data="kw_save")]
            ])
        )

    elif state == "welcome":
        async with SessionLocal() as db:
            res = await db.execute(select(GroupConfig).where(GroupConfig.chat_id == str(m.chat.id)))
            cfg = res.scalar() or GroupConfig(chat_id=str(m.chat.id))

            cfg.welcome_text = text
            db.add(cfg)
            await db.commit()

        user_state.pop(uid, None)
        await m.answer("✅ Đã lưu welcome", reply_markup=home_menu())

    elif state == "post_text":
        temp[uid] = {"text": text}
        user_state[uid] = None

        await m.answer(
            "👁 Preview",
            reply_markup=smart_buttons(extra=[
                [InlineKeyboardButton(text="💾 Lưu", callback_data="post_save")]
            ])
        )

    else:
        # ===== KEYWORD MATCH =====
        kws = await get_keywords()
        for k in kws:
            if k.key in text.lower():
                await m.answer(k.text, reply_markup=smart_buttons(k.button))
                return

        # ===== AI =====
        cfg = await get_cfg(m.chat.id)
        if cfg and getattr(cfg, "auto_ai", 1) == 0:
            return

        reply = await ai_reply(text)
        await m.answer(reply, reply_markup=home_menu())

# ===== AUTO POST =====
async def send_post(chat_id, text, button=None):
    await bot.send_message(chat_id, text, reply_markup=smart_buttons(button))

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

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(f"{BASE_URL}/webhook")

    print("🚀 BOT READY")
