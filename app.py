import os
import time
import asyncio
import contextlib

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, String, Text, select, delete

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN or not BASE_URL or not DATABASE_URL:
    raise RuntimeError("Thiếu BOT_TOKEN / BASE_URL / DATABASE_URL trong file .env")

BASE_URL = BASE_URL.rstrip("/")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

worker_task = None
last_sent = {}

# ======================
# STATE
# ======================
user_state = {}
temp = {}

def reset(uid):
    user_state.pop(uid, None)
    temp.pop(uid, None)

# ======================
# BUTTON
# Format:
# Text - https://url.com && Text 2 - https://url2.com
# Dòng mới = hàng mới
# ======================
def parse_buttons(text):
    if not text:
        return None

    rows = []
    for line in text.split("\n"):
        row = []
        for part in line.split("&&"):
            part = part.strip()
            if not part:
                continue

            if " - " in part:
                t, u = part.split(" - ", 1)
            elif "-" in part:
                t, u = part.split("-", 1)
            else:
                continue

            row.append({"text": t.strip(), "url": u.strip()})
        if row:
            rows.append(row)

    return rows or None

def build_buttons(data):
    if not data:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=b["text"], url=b["url"]) for b in row]
            for row in data
        ]
    )

# ======================
# MODELS
# ======================
class Keyword(Base):
    __tablename__ = "keywords"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, index=True)
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
    interval = Column(Integer, default=10)  # phút
    is_active = Column(Integer, default=0)
    pin = Column(Integer, default=0)

# ======================
# MENU
# ======================
def home():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📌 Từ khoá", callback_data="kw_menu")],
        [InlineKeyboardButton(text="📅 Auto Post", callback_data="auto_menu")]
    ])

def kw_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Thêm", callback_data="kw_add")],
        [InlineKeyboardButton(text="📋 List", callback_data="kw_list")],
        [InlineKeyboardButton(text="🔙 Home", callback_data="home")]
    ])

def auto_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Tạo", callback_data="auto_add")],
        [InlineKeyboardButton(text="📋 List", callback_data="auto_list")],
        [InlineKeyboardButton(text="🔙 Home", callback_data="home")]
    ])

# ======================
# HELPERS
# ======================
async def safe_edit(message: types.Message, text: str, reply_markup=None):
    try:
        return await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        return await message.answer(text, reply_markup=reply_markup)
    except Exception:
        return await message.answer(text, reply_markup=reply_markup)

async def send_preview(chat_id, text=None, image=None, button=None):
    kb = build_buttons(parse_buttons(button))

    if image:
        return await bot.send_photo(
            chat_id=chat_id,
            photo=image,
            caption=text or "",
            reply_markup=kb
        )

    return await bot.send_message(
        chat_id=chat_id,
        text=text or " ",
        reply_markup=kb
    )

def extract_image_from_message(m: types.Message):
    if m.photo:
        return m.photo[-1].file_id
    if m.text:
        return m.text.strip()
    return None

async def show_kw_list(message: types.Message):
    async with SessionLocal() as db:
        kws = (await db.execute(select(Keyword).order_by(Keyword.id.desc()))).scalars().all()

    kb = []
    for k in kws:
        kb.append([
            InlineKeyboardButton(text=k.key, callback_data=f"kw_view_{k.id}"),
            InlineKeyboardButton(text="❌", callback_data=f"kw_del_{k.id}")
        ])
    kb.append([InlineKeyboardButton(text="🔙", callback_data="kw_menu")])

    await safe_edit(
        message,
        "Danh sách keyword" if kws else "Chưa có keyword nào.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

async def show_kw_view(message: types.Message, kid: int):
    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)

    if not k:
        return await message.answer("Keyword không tồn tại.")

    await safe_edit(
        message,
        f"Keyword: {k.key}\n"
        f"Text: {'Có' if k.text else 'Trống'}\n"
        f"Ảnh: {'Có' if k.image else 'Không'}\n"
        f"Nút: {'Có' if k.button else 'Không'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Text", callback_data=f"kw_text_{k.id}")],
            [InlineKeyboardButton(text="🖼 Ảnh", callback_data=f"kw_img_{k.id}")],
            [InlineKeyboardButton(text="🔘 Nút", callback_data=f"kw_btn_{k.id}")],
            [InlineKeyboardButton(text="👁 Preview", callback_data=f"kw_pre_{k.id}")],
            [InlineKeyboardButton(text="❌ Xoá", callback_data=f"kw_del_{k.id}")],
            [InlineKeyboardButton(text="🔙", callback_data="kw_list")]
        ])
    )

async def show_auto_list(message: types.Message):
    async with SessionLocal() as db:
        posts = (await db.execute(select(AutoPost).order_by(AutoPost.id.desc()))).scalars().all()

    kb = []
    for p in posts:
        kb.append([
            InlineKeyboardButton(
                text=f"Post {p.id} ({'ON' if p.is_active else 'OFF'})",
                callback_data=f"auto_view_{p.id}"
            ),
            InlineKeyboardButton(text="❌", callback_data=f"auto_del_{p.id}")
        ])
    kb.append([InlineKeyboardButton(text="🔙", callback_data="auto_menu")])

    await safe_edit(
        message,
        "Danh sách auto post" if posts else "Chưa có auto post nào.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

async def show_auto_view(message: types.Message, pid: int):
    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)

    if not p:
        return await message.answer("Auto post không tồn tại.")

    await safe_edit(
        message,
        f"Post {p.id}\n"
        f"Chat ID: {p.chat_id}\n"
        f"Interval: {p.interval} phút\n"
        f"Active: {'ON' if p.is_active else 'OFF'}\n"
        f"Pin: {'ON' if p.pin else 'OFF'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"⚡ {'ON' if p.is_active else 'OFF'}", callback_data=f"auto_toggle_{p.id}")],
            [InlineKeyboardButton(text=f"📌 Ghim {'ON' if p.pin else 'OFF'}", callback_data=f"auto_pin_{p.id}")],
            [InlineKeyboardButton(text="✏️ Text", callback_data=f"auto_text_{p.id}")],
            [InlineKeyboardButton(text="🖼 Ảnh", callback_data=f"auto_img_{p.id}")],
            [InlineKeyboardButton(text="🔘 Nút", callback_data=f"auto_btn_{p.id}")],
            [InlineKeyboardButton(text="💬 Chat ID", callback_data=f"auto_chat_{p.id}")],
            [InlineKeyboardButton(text="⏱ Interval", callback_data=f"auto_int_{p.id}")],
            [InlineKeyboardButton(text="👁 Preview", callback_data=f"auto_pre_{p.id}")],
            [InlineKeyboardButton(text="❌ Xoá", callback_data=f"auto_del_{p.id}")],
            [InlineKeyboardButton(text="🔙", callback_data="auto_list")]
        ])
    )

# ======================
# START / HOME
# ======================
@dp.message(F.text == "/start")
async def start(m: types.Message):
    reset(m.from_user.id)
    await m.answer("🚀 Menu", reply_markup=home())

@dp.message(F.text == "/cancel")
async def cancel(m: types.Message):
    reset(m.from_user.id)
    await m.answer("Đã huỷ thao tác.", reply_markup=home())

@dp.callback_query(F.data == "home")
async def go_home(c: types.CallbackQuery):
    await c.answer()
    await safe_edit(c.message, "🚀 Menu", reply_markup=home())

# ======================
# KEYWORD
# ======================
@dp.callback_query(F.data == "kw_menu")
async def kw_menu(c: types.CallbackQuery):
    await c.answer()
    await safe_edit(c.message, "📌 Keyword", reply_markup=kw_menu_kb())

@dp.callback_query(F.data == "kw_add")
async def kw_add(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    user_state[uid] = "kw_add_key"
    temp[uid] = {}
    await c.message.answer("Nhập từ khoá mới:")

@dp.callback_query(F.data == "kw_list")
async def kw_list(c: types.CallbackQuery):
    await c.answer()
    await show_kw_list(c.message)

@dp.callback_query(F.data.startswith("kw_view_"))
async def kw_view(c: types.CallbackQuery):
    await c.answer()
    kid = int(c.data.split("_")[-1])
    await show_kw_view(c.message, kid)

@dp.callback_query(F.data.startswith("kw_text_"))
async def kw_text(c: types.CallbackQuery):
    await c.answer()
    kid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "kw_edit_text"
    temp[uid] = {"id": kid}
    await c.message.answer("Nhập nội dung text cho keyword:")

@dp.callback_query(F.data.startswith("kw_img_"))
async def kw_img(c: types.CallbackQuery):
    await c.answer()
    kid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "kw_edit_image"
    temp[uid] = {"id": kid}
    await c.message.answer("Gửi ảnh hoặc nhập URL/file_id ảnh:")

@dp.callback_query(F.data.startswith("kw_btn_"))
async def kw_btn(c: types.CallbackQuery):
    await c.answer()
    kid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "kw_edit_button"
    temp[uid] = {"id": kid}
    await c.message.answer(
        "Nhập nút theo format:\n"
        "Google - https://google.com && YouTube - https://youtube.com\n"
        "Dòng mới = hàng mới"
    )

@dp.callback_query(F.data.startswith("kw_pre_"))
async def kw_pre(c: types.CallbackQuery):
    await c.answer()
    kid = int(c.data.split("_")[-1])

    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)

    if not k:
        return await c.message.answer("Keyword không tồn tại.")

    await send_preview(
        chat_id=c.from_user.id,
        text=k.text,
        image=k.image,
        button=k.button
    )

@dp.callback_query(F.data.startswith("kw_del_"))
async def kw_del(c: types.CallbackQuery):
    await c.answer("Đã xoá")
    kid = int(c.data.split("_")[-1])

    async with SessionLocal() as db:
        await db.execute(delete(Keyword).where(Keyword.id == kid))
        await db.commit()

    await show_kw_list(c.message)

# ======================
# AUTO POST
# ======================
@dp.callback_query(F.data == "auto_menu")
async def auto_menu(c: types.CallbackQuery):
    await c.answer()
    await safe_edit(c.message, "📅 Auto Post", reply_markup=auto_menu_kb())

@dp.callback_query(F.data == "auto_add")
async def auto_add(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    user_state[uid] = "auto_add_chat"
    temp[uid] = {}
    await c.message.answer("Nhập chat_id để tạo auto post:")

@dp.callback_query(F.data == "auto_list")
async def auto_list(c: types.CallbackQuery):
    await c.answer()
    await show_auto_list(c.message)

@dp.callback_query(F.data.startswith("auto_view_"))
async def auto_view(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])
    await show_auto_view(c.message, pid)

@dp.callback_query(F.data.startswith("auto_toggle_"))
async def auto_toggle(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])

    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)
        if not p:
            return await c.message.answer("Không tìm thấy post.")
        p.is_active = 0 if p.is_active else 1
        await db.commit()

    await show_auto_view(c.message, pid)

@dp.callback_query(F.data.startswith("auto_pin_"))
async def auto_pin(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])

    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)
        if not p:
            return await c.message.answer("Không tìm thấy post.")
        p.pin = 0 if p.pin else 1
        await db.commit()

    await show_auto_view(c.message, pid)

@dp.callback_query(F.data.startswith("auto_text_"))
async def auto_text(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "auto_edit_text"
    temp[uid] = {"id": pid}
    await c.message.answer("Nhập text cho auto post:")

@dp.callback_query(F.data.startswith("auto_img_"))
async def auto_img(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "auto_edit_image"
    temp[uid] = {"id": pid}
    await c.message.answer("Gửi ảnh hoặc nhập URL/file_id ảnh:")

@dp.callback_query(F.data.startswith("auto_btn_"))
async def auto_btn(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "auto_edit_button"
    temp[uid] = {"id": pid}
    await c.message.answer(
        "Nhập nút theo format:\n"
        "Google - https://google.com && YouTube - https://youtube.com\n"
        "Dòng mới = hàng mới"
    )

@dp.callback_query(F.data.startswith("auto_chat_"))
async def auto_chat(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "auto_edit_chat"
    temp[uid] = {"id": pid}
    await c.message.answer("Nhập chat_id mới:")

@dp.callback_query(F.data.startswith("auto_int_"))
async def auto_int(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "auto_edit_interval"
    temp[uid] = {"id": pid}
    await c.message.answer("Nhập interval (phút):")

@dp.callback_query(F.data.startswith("auto_pre_"))
async def auto_pre(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])

    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)

    if not p:
        return await c.message.answer("Auto post không tồn tại.")

    await send_preview(
        chat_id=c.from_user.id,
        text=p.text,
        image=p.image,
        button=p.button
    )

@dp.callback_query(F.data.startswith("auto_del_"))
async def auto_del(c: types.CallbackQuery):
    await c.answer("Đã xoá")
    pid = int(c.data.split("_")[-1])

    async with SessionLocal() as db:
        await db.execute(delete(AutoPost).where(AutoPost.id == pid))
        await db.commit()

    await show_auto_list(c.message)

# ======================
# MESSAGE HANDLER FOR STATE + KEYWORD REPLY
# ======================
@dp.message()
async def all_messages(m: types.Message):
    uid = m.from_user.id
    state = user_state.get(uid)

    # ===== STATE MODE =====
    if state == "kw_add_key":
        key = (m.text or "").strip()
        if not key:
            return await m.answer("Từ khoá không được để trống.")

        async with SessionLocal() as db:
            exists = (await db.execute(select(Keyword).where(Keyword.key == key))).scalars().first()
            if exists:
                return await m.answer("Keyword đã tồn tại, nhập keyword khác.")

            db.add(Keyword(key=key, text="", image="", button=""))
            await db.commit()

        reset(uid)
        return await m.answer(f"Đã tạo keyword: {key}", reply_markup=home())

    if state == "kw_edit_text":
        kid = temp[uid]["id"]
        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if k:
                k.text = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật text keyword.", reply_markup=home())

    if state == "kw_edit_image":
        kid = temp[uid]["id"]
        image = extract_image_from_message(m)
        if not image:
            return await m.answer("Vui lòng gửi ảnh hoặc URL/file_id ảnh.")
        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if k:
                k.image = image
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật ảnh keyword.", reply_markup=home())

    if state == "kw_edit_button":
        kid = temp[uid]["id"]
        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if k:
                k.button = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật nút keyword.", reply_markup=home())

    if state == "auto_add_chat":
        chat_id = (m.text or "").strip()
        if not chat_id:
            return await m.answer("Chat ID không được để trống.")

        async with SessionLocal() as db:
            post = AutoPost(
                chat_id=chat_id,
                text="",
                image="",
                button="",
                interval=10,
                is_active=0,
                pin=0
            )
            db.add(post)
            await db.commit()

        reset(uid)
        return await m.answer("Đã tạo auto post mới.", reply_markup=home())

    if state == "auto_edit_text":
        pid = temp[uid]["id"]
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.text = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật text auto post.", reply_markup=home())

    if state == "auto_edit_image":
        pid = temp[uid]["id"]
        image = extract_image_from_message(m)
        if not image:
            return await m.answer("Vui lòng gửi ảnh hoặc URL/file_id ảnh.")
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.image = image
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật ảnh auto post.", reply_markup=home())

    if state == "auto_edit_button":
        pid = temp[uid]["id"]
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.button = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật nút auto post.", reply_markup=home())

    if state == "auto_edit_chat":
        pid = temp[uid]["id"]
        chat_id = (m.text or "").strip()
        if not chat_id:
            return await m.answer("Chat ID không hợp lệ.")
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.chat_id = chat_id
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật chat_id.", reply_markup=home())

    if state == "auto_edit_interval":
        pid = temp[uid]["id"]
        try:
            interval = int((m.text or "").strip())
            if interval <= 0:
                raise ValueError
        except ValueError:
            return await m.answer("Interval phải là số nguyên dương.")

        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.interval = interval
                await db.commit()

        reset(uid)
        return await m.answer("Đã cập nhật interval.", reply_markup=home())

    # ===== KEYWORD AUTO REPLY =====
    text = (m.text or "").strip()
    if not text or text.startswith("/"):
        return

    async with SessionLocal() as db:
        k = (await db.execute(select(Keyword).where(Keyword.key == text))).scalars().first()

    if k:
        await send_preview(
            chat_id=m.chat.id,
            text=k.text,
            image=k.image,
            button=k.button
        )

# ======================
# AUTO WORKER
# ======================
async def auto_worker():
    while True:
        try:
            now = time.time()

            async with SessionLocal() as db:
                posts = (
                    await db.execute(
                        select(AutoPost).where(AutoPost.is_active == 1)
                    )
                ).scalars().all()

            for p in posts:
                interval_sec = max((p.interval or 10), 1) * 60
                last_time = last_sent.get(p.id, 0)

                if now - last_time < interval_sec:
                    continue

                try:
                    msg = await send_preview(
                        chat_id=p.chat_id,
                        text=p.text,
                        image=p.image,
                        button=p.button
                    )
                    last_sent[p.id] = now

                    if p.pin:
                        try:
                            await bot.pin_chat_message(
                                chat_id=p.chat_id,
                                message_id=msg.message_id
                            )
                        except Exception as e:
                            print(f"Pin lỗi post {p.id}: {e}")

                except Exception as e:
                    print(f"Gửi auto post {p.id} lỗi: {e}")

        except Exception as e:
            print(f"auto_worker lỗi: {e}")

        await asyncio.sleep(10)

# ======================
# FASTAPI
# ======================
@app.get("/")
async def root():
    return {"status": "ok"}

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = types.Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.on_event("startup")
async def startup():
    global worker_task

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    worker_task = asyncio.create_task(auto_worker())

    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(f"{BASE_URL}/webhook")

    print("READY")

@app.on_event("shutdown")
async def shutdown():
    global worker_task

    if worker_task:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task

    with contextlib.suppress(Exception):
        await bot.delete_webhook()

    await bot.session.close()
