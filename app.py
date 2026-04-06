python
import os
import time
import asyncio
import contextlib
from datetime import datetime

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, String, Text, select, delete, text as sql_text

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN or not BASE_URL or not DATABASE_URL:
    raise RuntimeError("Thiếu BOT_TOKEN / BASE_URL / DATABASE_URL")

BASE_URL = BASE_URL.rstrip("/")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

worker_task = None
last_sent = {}

user_state = {}
temp = {}

def reset(uid):
    user_state.pop(uid, None)
    temp.pop(uid, None)

# ======================
# BUTTON PARSER
# Format:
# Nút - https://url.com && Nút 2 - https://url2.com
# dòng mới = hàng mới
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
    mode = Column(String, default="exact")   # exact / contains
    active = Column(Integer, default=1)
    text = Column(Text, default="")
    image = Column(Text, default="")
    button = Column(Text, default="")

class WelcomeSetting(Base):
    __tablename__ = "welcome_settings"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, unique=True, index=True)
    active = Column(Integer, default=0)
    text = Column(Text, default="")
    image = Column(Text, default="")
    button = Column(Text, default="")
    delete_after = Column(Integer, default=0)  # phút
    pin = Column(Integer, default=0)

class AutoPost(Base):
    __tablename__ = "auto_post"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, index=True)
    active = Column(Integer, default=0)
    text = Column(Text, default="")
    image = Column(Text, default="")
    button = Column(Text, default="")
    interval = Column(Integer, default=10)
    pin = Column(Integer, default=0)
    start_at = Column(Text, default="")
    end_at = Column(Text, default="")
    last_sent_ts = Column(Integer, default=0)

# ======================
# KEYBOARDS
# ======================
def home():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📌 Từ khoá", callback_data="kw_menu")],
        [InlineKeyboardButton(text="👋 Chào mừng nhóm", callback_data="wl_menu")],
        [InlineKeyboardButton(text="📅 Auto Post", callback_data="auto_menu")],
    ])

def kw_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Thêm", callback_data="kw_add")],
        [InlineKeyboardButton(text="📋 Danh sách", callback_data="kw_list")],
        [InlineKeyboardButton(text="🔙 Home", callback_data="home")],
    ])

def wl_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Tạo", callback_data="wl_add")],
        [InlineKeyboardButton(text="📋 Danh sách", callback_data="wl_list")],
        [InlineKeyboardButton(text="🔙 Home", callback_data="home")],
    ])

def auto_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Tạo", callback_data="auto_add")],
        [InlineKeyboardButton(text="📋 Danh sách", callback_data="auto_list")],
        [InlineKeyboardButton(text="🔙 Home", callback_data="home")],
    ])

# ======================
# HELPERS
# ======================
async def safe_edit(message: types.Message, text_: str, reply_markup=None):
    try:
        return await message.edit_text(text_, reply_markup=reply_markup)
    except TelegramBadRequest:
        return await message.answer(text_, reply_markup=reply_markup)
    except Exception:
        return await message.answer(text_, reply_markup=reply_markup)

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

def parse_dt(s: str):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M")
    except Exception:
        return None

# ======================
# DB INIT
# ======================
async def ensure_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        await conn.execute(sql_text("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS mode VARCHAR DEFAULT 'exact'"))
        await conn.execute(sql_text("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS active INTEGER DEFAULT 1"))
        await conn.execute(sql_text("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS text TEXT DEFAULT ''"))
        await conn.execute(sql_text("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS image TEXT DEFAULT ''"))
        await conn.execute(sql_text("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS button TEXT DEFAULT ''"))

        await conn.execute(sql_text("ALTER TABLE welcome_settings ADD COLUMN IF NOT EXISTS active INTEGER DEFAULT 0"))
        await conn.execute(sql_text("ALTER TABLE welcome_settings ADD COLUMN IF NOT EXISTS text TEXT DEFAULT ''"))
        await conn.execute(sql_text("ALTER TABLE welcome_settings ADD COLUMN IF NOT EXISTS image TEXT DEFAULT ''"))
        await conn.execute(sql_text("ALTER TABLE welcome_settings ADD COLUMN IF NOT EXISTS button TEXT DEFAULT ''"))
        await conn.execute(sql_text("ALTER TABLE welcome_settings ADD COLUMN IF NOT EXISTS delete_after INTEGER DEFAULT 0"))
        await conn.execute(sql_text("ALTER TABLE welcome_settings ADD COLUMN IF NOT EXISTS pin INTEGER DEFAULT 0"))

        await conn.execute(sql_text("ALTER TABLE auto_post ADD COLUMN IF NOT EXISTS active INTEGER DEFAULT 0"))
        await conn.execute(sql_text("ALTER TABLE auto_post ADD COLUMN IF NOT EXISTS text TEXT DEFAULT ''"))
        await conn.execute(sql_text("ALTER TABLE auto_post ADD COLUMN IF NOT EXISTS image TEXT DEFAULT ''"))
        await conn.execute(sql_text("ALTER TABLE auto_post ADD COLUMN IF NOT EXISTS button TEXT DEFAULT ''"))
        await conn.execute(sql_text("ALTER TABLE auto_post ADD COLUMN IF NOT EXISTS interval INTEGER DEFAULT 10"))
        await conn.execute(sql_text("ALTER TABLE auto_post ADD COLUMN IF NOT EXISTS pin INTEGER DEFAULT 0"))
        await conn.execute(sql_text("ALTER TABLE auto_post ADD COLUMN IF NOT EXISTS start_at TEXT DEFAULT ''"))
        await conn.execute(sql_text("ALTER TABLE auto_post ADD COLUMN IF NOT EXISTS end_at TEXT DEFAULT ''"))
        await conn.execute(sql_text("ALTER TABLE auto_post ADD COLUMN IF NOT EXISTS last_sent_ts INTEGER DEFAULT 0"))

# ======================
# VIEW FUNCTIONS
# ======================
async def show_kw_list(message):
    async with SessionLocal() as db:
        rows = (await db.execute(select(Keyword).order_by(Keyword.id.desc()))).scalars().all()

    kb = []
    for k in rows:
        kb.append([
            InlineKeyboardButton(
                text=f"{k.key} ({'✅' if k.active else '❌'})",
                callback_data=f"kw_view_{k.id}"
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"kw_del_{k.id}")
        ])
    kb.append([InlineKeyboardButton(text="🔙", callback_data="kw_menu")])

    await safe_edit(
        message,
        "Chi tiết cài đặt từ khoá" if rows else "Chưa có từ khoá nào.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

async def show_kw_view(message, kid):
    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)
    if not k:
        return await message.answer("Keyword không tồn tại.")

    await safe_edit(
        message,
        f"Chi tiết cài đặt từ khoá\n\n"
        f"Từ khoá: {k.key}\n\n"
        f"Chế độ kích hoạt: {'Chính xác' if k.mode == 'exact' else 'Bao gồm'}\n"
        f"Phản hồi: {'✅' if k.active else '❌'}\n"
        f"Nút: {'✅' if k.button else '❌'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Chế độ: {'✅ Chính xác' if k.mode == 'exact' else 'Bao gồm'}", callback_data=f"kw_mode_{k.id}")],
            [InlineKeyboardButton(text=f"Trạng thái: {'✅ Mở' if k.active else '❌ Đóng'}", callback_data=f"kw_toggle_{k.id}")],
            [InlineKeyboardButton(text="📝 Sửa đổi từ khóa", callback_data=f"kw_key_{k.id}")],
            [InlineKeyboardButton(text="📝 Sửa đổi văn bản", callback_data=f"kw_text_{k.id}")],
            [InlineKeyboardButton(text="🖼 Sửa đổi phương tiện", callback_data=f"kw_img_{k.id}")],
            [InlineKeyboardButton(text="🔤 Nút sửa đổi", callback_data=f"kw_btn_{k.id}")],
            [InlineKeyboardButton(text="👀 Thông báo xem trước", callback_data=f"kw_pre_{k.id}")],
            [InlineKeyboardButton(text="⬅️ Trở lại", callback_data="kw_list")],
        ])
    )

async def show_wl_list(message):
    async with SessionLocal() as db:
        rows = (await db.execute(select(WelcomeSetting).order_by(WelcomeSetting.id.desc()))).scalars().all()

    kb = []
    for w in rows:
        kb.append([
            InlineKeyboardButton(
                text=f"{w.chat_id} ({'✅' if w.active else '❌'})",
                callback_data=f"wl_view_{w.id}"
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"wl_del_{w.id}")
        ])
    kb.append([InlineKeyboardButton(text="🔙", callback_data="wl_menu")])

    await safe_edit(
        message,
        "Chào mừng nhóm" if rows else "Chưa có cấu hình chào mừng nào.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

async def show_wl_view(message, wid):
    async with SessionLocal() as db:
        w = await db.get(WelcomeSetting, wid)
    if not w:
        return await message.answer("Cấu hình chào mừng không tồn tại.")

    await safe_edit(
        message,
        f"Chào mừng nhóm\n\n"
        f"Trạng thái: {'Mở' if w.active else 'Đóng'}\n"
        f"Xóa tin nhắn (phút): {w.delete_after if w.delete_after else 'Không'}\n"
        f"Nội dung chào mừng tùy chỉnh:\n"
        f"├ Ảnh phương tiện: {'✅' if w.image else '❌'}\n"
        f"├ Nút liên kết: {'✅' if w.button else '❌'}\n"
        f"└ Nội dung văn bản: {'✅' if w.text else '❌'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Trạng thái: {'✅ Mở' if w.active else '❌ Đóng'}", callback_data=f"wl_toggle_{w.id}")],
            [InlineKeyboardButton(text=f"Xóa tin nhắn... {'✅ là' if w.delete_after else '❌ Không'}", callback_data=f"wl_delmin_{w.id}")],
            [InlineKeyboardButton(text="📝 Sửa đổi văn bản", callback_data=f"wl_text_{w.id}")],
            [InlineKeyboardButton(text="📷 Sửa đổi phương tiện", callback_data=f"wl_img_{w.id}")],
            [InlineKeyboardButton(text="🔤 Nút sửa đổi", callback_data=f"wl_btn_{w.id}")],
            [InlineKeyboardButton(text="👀 Thông báo xem trước", callback_data=f"wl_pre_{w.id}")],
            [InlineKeyboardButton(text="⬅️ Trở lại", callback_data="wl_list")],
        ])
    )

async def show_auto_list(message):
    async with SessionLocal() as db:
        rows = (await db.execute(select(AutoPost).order_by(AutoPost.id.desc()))).scalars().all()

    kb = []
    for p in rows:
        kb.append([
            InlineKeyboardButton(
                text=f"{p.id} ({'✅' if p.active else '❌'})",
                callback_data=f"auto_view_{p.id}"
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"auto_del_{p.id}")
        ])
    kb.append([InlineKeyboardButton(text="🔙", callback_data="auto_menu")])

    await safe_edit(
        message,
        "Tin nhắn định kỳ" if rows else "Chưa có auto post nào.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

async def show_auto_view(message, pid):
    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)
    if not p:
        return await message.answer("Auto post không tồn tại.")

    await safe_edit(
        message,
        f"Tin nhắn định kỳ\n\n"
        f"Trạng thái: {'✅ Mở' if p.active else '❌ Đóng'}\n"
        f"Khoảng thời gian lặp lại: {p.interval} phút\n"
        f"Khoảng thời gian: {p.start_at or '-'}\n"
        f"Lần chạy tiếp theo: {'-' if not p.last_sent_ts else p.last_sent_ts}\n"
        f"Ngày bắt đầu: {p.start_at or '-'}\n"
        f"Ngày kết thúc: {p.end_at or '-'}\n\n"
        f"Hình ảnh phương tiện: {'✅' if p.image else '❌'}\n"
        f"Nút liên kết: {'✅' if p.button else '❌'}\n"
        f"Nội dung văn bản: {'✅' if p.text else '❌'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Trạng thái: {'✅ Mở' if p.active else '❌ Đóng'}", callback_data=f"auto_toggle_{p.id}")],
            [InlineKeyboardButton(text=f"📌 Ghim lại: {'✅ Có' if p.pin else '❌ Không'}", callback_data=f"auto_pin_{p.id}")],
            [InlineKeyboardButton(text="📝 Sửa đổi văn bản", callback_data=f"auto_text_{p.id}")],
            [InlineKeyboardButton(text="📷 Sửa đổi phương tiện", callback_data=f"auto_img_{p.id}")],
            [InlineKeyboardButton(text="🔤 Nút sửa đổi", callback_data=f"auto_btn_{p.id}")],
            [InlineKeyboardButton(text="👀 Thông báo xem trước", callback_data=f"auto_pre_{p.id}")],
            [InlineKeyboardButton(text="⏩ Thời gian giữa các...", callback_data=f"auto_int_{p.id}")],
            [InlineKeyboardButton(text="🕘 Đặt khoảng thời gian", callback_data=f"auto_time_{p.id}")],
            [InlineKeyboardButton(text="📅 Ngày bắt đầu", callback_data=f"auto_start_{p.id}")],
            [InlineKeyboardButton(text="📅 Ngày kết thúc", callback_data=f"auto_end_{p.id}")],
            [InlineKeyboardButton(text="⬅️ Trở lại", callback_data="auto_list")],
        ])
    )

# ======================
# START
# ======================
@dp.message(F.text == "/start")
async def start(m: types.Message):
    if not m.from_user:
        return
    reset(m.from_user.id)
    await m.answer("🏠 Trang chủ", reply_markup=home())

@dp.message(F.text == "/cancel")
async def cancel(m: types.Message):
    if not m.from_user:
        return
    reset(m.from_user.id)
    await m.answer("Đã huỷ thao tác.", reply_markup=home())

@dp.callback_query(F.data == "home")
async def go_home(c: types.CallbackQuery):
    await c.answer()
    await safe_edit(c.message, "🏠 Trang chủ", reply_markup=home())

# ======================
# KEYWORD MENU
# ======================
@dp.callback_query(F.data == "kw_menu")
async def kw_menu(c: types.CallbackQuery):
    await c.answer()
    await safe_edit(c.message, "📌 Từ khoá", reply_markup=kw_menu_kb())

@dp.callback_query(F.data == "kw_add")
async def kw_add(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    user_state[uid] = "kw_add_key"
    temp[uid] = {}
    await c.message.answer("Vui lòng gửi từ khóa (nếu có nhiều từ khóa, vui lòng xuống dòng):")

@dp.callback_query(F.data == "kw_list")
async def kw_list(c: types.CallbackQuery):
    await c.answer()
    await show_kw_list(c.message)

@dp.callback_query(F.data.startswith("kw_view_"))
async def kw_view(c: types.CallbackQuery):
    await c.answer()
    await show_kw_view(c.message, int(c.data.split("_")[-1]))

@dp.callback_query(F.data.startswith("kw_toggle_"))
async def kw_toggle(c: types.CallbackQuery):
    await c.answer()
    kid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)
        if not k:
            return await c.message.answer("Không tìm thấy keyword.")
        k.active = 0 if k.active else 1
        await db.commit()
    await show_kw_view(c.message, kid)

@dp.callback_query(F.data.startswith("kw_mode_"))
async def kw_mode(c: types.CallbackQuery):
    await c.answer()
    kid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)
        if not k:
            return await c.message.answer("Không tìm thấy keyword.")
        k.mode = "contains" if k.mode == "exact" else "exact"
        await db.commit()
    await show_kw_view(c.message, kid)

@dp.callback_query(F.data.startswith("kw_key_"))
async def kw_key(c: types.CallbackQuery):
    await c.answer()
    kid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "kw_edit_key"
    temp[uid] = {"id": kid}
    await c.message.answer("Nhập từ khóa mới:")

@dp.callback_query(F.data.startswith("kw_text_"))
async def kw_text(c: types.CallbackQuery):
    await c.answer()
    kid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "kw_edit_text"
    temp[uid] = {"id": kid}
    await c.message.answer("Vui lòng nhập văn bản, hình ảnh, hoặc nội dung hình ảnh + văn bản")

@dp.callback_query(F.data.startswith("kw_img_"))
async def kw_img(c: types.CallbackQuery):
    await c.answer()
    kid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "kw_edit_image"
    temp[uid] = {"id": kid}
    await c.message.answer("Vui lòng nhập ảnh hoặc URL/file_id ảnh:")

@dp.callback_query(F.data.startswith("kw_btn_"))
async def kw_btn(c: types.CallbackQuery):
    await c.answer()
    kid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "kw_edit_button"
    temp[uid] = {"id": kid}
    await c.message.answer(
        "Nút thiết lập\n\n"
        "Gợi ý:\n"
        "1. - Bên trái dấu gạch ngang là tên nút, bên phải là liên kết\n"
        "2. && được sử dụng để ngăn cách nhiều nút trong cùng một dòng\n"
        "3. Xuống dòng có thể giúp nút bắt đầu dòng mới\n\n"
        "Ví dụ:\n"
        "Tên liên kết-https://t.me/xx"
    )

@dp.callback_query(F.data.startswith("kw_pre_"))
async def kw_pre(c: types.CallbackQuery):
    await c.answer()
    kid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)
    if not k:
        return await c.message.answer("Keyword không tồn tại.")
    await send_preview(chat_id=c.from_user.id, text=k.text, image=k.image, button=k.button)

@dp.callback_query(F.data.startswith("kw_del_"))
async def kw_del(c: types.CallbackQuery):
    await c.answer("Đã xoá")
    kid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        await db.execute(delete(Keyword).where(Keyword.id == kid))
        await db.commit()
    await show_kw_list(c.message)

# ======================
# WELCOME MENU
# ======================
@dp.callback_query(F.data == "wl_menu")
async def wl_menu(c: types.CallbackQuery):
    await c.answer()
    await safe_edit(c.message, "👋 Chào mừng nhóm", reply_markup=wl_menu_kb())

@dp.callback_query(F.data == "wl_add")
async def wl_add(c: types.CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    user_state[uid] = "wl_add_chat"
    temp[uid] = {}
    await c.message.answer("Vui lòng gửi chat_id của nhóm:")

@dp.callback_query(F.data == "wl_list")
async def wl_list(c: types.CallbackQuery):
    await c.answer()
    await show_wl_list(c.message)

@dp.callback_query(F.data.startswith("wl_view_"))
async def wl_view(c: types.CallbackQuery):
    await c.answer()
    await show_wl_view(c.message, int(c.data.split("_")[-1]))

@dp.callback_query(F.data.startswith("wl_toggle_"))
async def wl_toggle(c: types.CallbackQuery):
    await c.answer()
    wid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        w = await db.get(WelcomeSetting, wid)
        if not w:
            return await c.message.answer("Không tìm thấy.")
        w.active = 0 if w.active else 1
        await db.commit()
    await show_wl_view(c.message, wid)

@dp.callback_query(F.data.startswith("wl_text_"))
async def wl_text(c: types.CallbackQuery):
    await c.answer()
    wid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "wl_edit_text"
    temp[uid] = {"id": wid}
    await c.message.answer("Vui lòng nhập văn bản, hình ảnh, hoặc nội dung hình ảnh + văn bản")

@dp.callback_query(F.data.startswith("wl_img_"))
async def wl_img(c: types.CallbackQuery):
    await c.answer()
    wid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "wl_edit_image"
    temp[uid] = {"id": wid}
    await c.message.answer("Vui lòng nhập ảnh hoặc URL/file_id ảnh:")

@dp.callback_query(F.data.startswith("wl_btn_"))
async def wl_btn(c: types.CallbackQuery):
    await c.answer()
    wid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "wl_edit_button"
    temp[uid] = {"id": wid}
    await c.message.answer(
        "Nút thiết lập\n\n"
        "Gợi ý:\n"
        "1. - Bên trái dấu gạch ngang là tên nút, bên phải là liên kết\n"
        "2. && được sử dụng để ngăn cách nhiều nút trong cùng một dòng\n"
        "3. Xuống dòng có thể giúp nút bắt đầu dòng mới"
    )

@dp.callback_query(F.data.startswith("wl_delmin_"))
async def wl_delmin(c: types.CallbackQuery):
    await c.answer()
    wid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "wl_edit_delete_after"
    temp[uid] = {"id": wid}
    await c.message.answer("Nhập số phút để xoá tin nhắn (0 = không xoá):")

@dp.callback_query(F.data.startswith("wl_pre_"))
async def wl_pre(c: types.CallbackQuery):
    await c.answer()
    wid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        w = await db.get(WelcomeSetting, wid)
    if not w:
        return await c.message.answer("Không tồn tại.")
    await send_preview(chat_id=c.from_user.id, text=w.text, image=w.image, button=w.button)

@dp.callback_query(F.data.startswith("wl_del_"))
async def wl_del(c: types.CallbackQuery):
    await c.answer("Đã xoá")
    wid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        await db.execute(delete(WelcomeSetting).where(WelcomeSetting.id == wid))
        await db.commit()
    await show_wl_list(c.message)

@dp.callback_query(F.data.startswith("wl_pin_"))
async def wl_pin(c: types.CallbackQuery):
    await c.answer()
    wid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        w = await db.get(WelcomeSetting, wid)
        if not w:
            return await c.message.answer("Không tồn tại.")
        w.pin = 0 if w.pin else 1
        await db.commit()
    await show_wl_view(c.message, wid)

# ======================
# AUTO MENU
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
    await show_auto_view(c.message, int(c.data.split("_")[-1]))

@dp.callback_query(F.data.startswith("auto_toggle_"))
async def auto_toggle(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)
        if not p:
            return await c.message.answer("Không tìm thấy post.")
        p.active = 0 if p.active else 1
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
    await c.message.answer("Nhập nội dung văn bản:")

@dp.callback_query(F.data.startswith("auto_img_"))
async def auto_img(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "auto_edit_image"
    temp[uid] = {"id": pid}
    await c.message.answer("Vui lòng nhập ảnh hoặc URL/file_id ảnh:")

@dp.callback_query(F.data.startswith("auto_btn_"))
async def auto_btn(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "auto_edit_button"
    temp[uid] = {"id": pid}
    await c.message.answer(
        "Nút thiết lập\n\n"
        "Gợi ý:\n"
        "1. - Bên trái dấu gạch ngang là tên nút, bên phải là liên kết\n"
        "2. && được sử dụng để ngăn cách nhiều nút trong cùng một dòng\n"
        "3. Xuống dòng có thể giúp nút bắt đầu dòng mới"
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
    await c.message.answer("Nhập khoảng thời gian lặp lại (phút):")

@dp.callback_query(F.data.startswith("auto_time_"))
async def auto_time(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "auto_edit_time"
    temp[uid] = {"id": pid}
    await c.message.answer("Nhập khoảng thời gian / ghi chú thời gian (nếu muốn):")

@dp.callback_query(F.data.startswith("auto_start_"))
async def auto_start(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "auto_edit_start"
    temp[uid] = {"id": pid}
    await c.message.answer("Nhập ngày bắt đầu: YYYY-MM-DD HH:MM")

@dp.callback_query(F.data.startswith("auto_end_"))
async def auto_end(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    user_state[uid] = "auto_edit_end"
    temp[uid] = {"id": pid}
    await c.message.answer("Nhập ngày kết thúc: YYYY-MM-DD HH:MM")

@dp.callback_query(F.data.startswith("auto_pre_"))
async def auto_pre(c: types.CallbackQuery):
    await c.answer()
    pid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)
    if not p:
        return await c.message.answer("Không tồn tại.")
    await send_preview(chat_id=c.from_user.id, text=p.text, image=p.image, button=p.button)

@dp.callback_query(F.data.startswith("auto_del_"))
async def auto_del(c: types.CallbackQuery):
    await c.answer("Đã xoá")
    pid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        await db.execute(delete(AutoPost).where(AutoPost.id == pid))
        await db.commit()
    await show_auto_list(c.message)

# ======================
# STATE HANDLER
# ======================
@dp.message()
async def all_messages(m: types.Message):
    if not m.from_user:
        return

    uid = m.from_user.id
    state = user_state.get(uid)

    # --- KW ---
    if state == "kw_add_key":
        key = (m.text or "").strip()
        if not key:
            return await m.answer("Từ khoá không được để trống.")
        async with SessionLocal() as db:
            exists = (await db.execute(select(Keyword).where(Keyword.key == key))).scalars().first()
            if exists:
                return await m.answer("Keyword đã tồn tại, nhập keyword khác.")
            db.add(Keyword(key=key, mode="exact", active=1))
            await db.commit()
        reset(uid)
        return await m.answer(f"Đã tạo keyword: {key}", reply_markup=home())

    if state == "kw_edit_key":
        kid = temp[uid]["id"]
        key = (m.text or "").strip()
        if not key:
            return await m.answer("Từ khoá không được để trống.")
        async with SessionLocal() as db:
            exists = (await db.execute(select(Keyword).where(Keyword.key == key, Keyword.id != kid))).scalars().first()
            if exists:
                return await m.answer("Keyword đã tồn tại, nhập keyword khác.")
            k = await db.get(Keyword, kid)
            if k:
                k.key = key
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật từ khoá.", reply_markup=home())

    if state == "kw_edit_text":
        kid = temp[uid]["id"]
        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if k:
                k.text = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật nội dung văn bản.", reply_markup=home())

    if state == "kw_edit_image":
        kid = temp[uid]["id"]
        image = extract_image_from_message(m)
        if not image:
            return await m.answer("Vui lòng nhập ảnh hoặc URL/file_id ảnh.")
        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if k:
                k.image = image
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật phương tiện.", reply_markup=home())

    if state == "kw_edit_button":
        kid = temp[uid]["id"]
        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if k:
                k.button = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật nút.", reply_markup=home())

    # --- WELCOME ---
    if state == "wl_add_chat":
        chat_id = (m.text or "").strip()
        if not chat_id:
            return await m.answer("Chat ID không được để trống.")
        async with SessionLocal() as db:
            exists = (await db.execute(select(WelcomeSetting).where(WelcomeSetting.chat_id == chat_id))).scalars().first()
            if exists:
                return await m.answer("Chat ID này đã tồn tại.")
            db.add(WelcomeSetting(chat_id=chat_id))
            await db.commit()
        reset(uid)
        return await m.answer("Đã tạo cấu hình chào mừng.", reply_markup=home())

    if state == "wl_edit_text":
        wid = temp[uid]["id"]
        async with SessionLocal() as db:
            w = await db.get(WelcomeSetting, wid)
            if w:
                w.text = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật văn bản.", reply_markup=home())

    if state == "wl_edit_image":
        wid = temp[uid]["id"]
        image = extract_image_from_message(m)
        if not image:
            return await m.answer("Vui lòng nhập ảnh hoặc URL/file_id ảnh.")
        async with SessionLocal() as db:
            w = await db.get(WelcomeSetting, wid)
            if w:
                w.image = image
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật phương tiện.", reply_markup=home())

    if state == "wl_edit_button":
        wid = temp[uid]["id"]
        async with SessionLocal() as db:
            w = await db.get(WelcomeSetting, wid)
            if w:
                w.button = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật nút.", reply_markup=home())

    if state == "wl_edit_delete_after":
        wid = temp[uid]["id"]
        try:
            minutes = int((m.text or "").strip())
            if minutes < 0:
                raise ValueError
        except ValueError:
            return await m.answer("Vui lòng nhập số nguyên >= 0.")
        async with SessionLocal() as db:
            w = await db.get(WelcomeSetting, wid)
            if w:
                w.delete_after = minutes
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật thời gian xoá.", reply_markup=home())

    # --- AUTO ---
    if state == "auto_add_chat":
        chat_id = (m.text or "").strip()
        if not chat_id:
            return await m.answer("Chat ID không được để trống.")
        async with SessionLocal() as db:
            db.add(AutoPost(chat_id=chat_id))
            await db.commit()
        reset(uid)
        return await m.answer("Đã tạo auto post.", reply_markup=home())

    if state == "auto_edit_text":
        pid = temp[uid]["id"]
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.text = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật nội dung văn bản.", reply_markup=home())

    if state == "auto_edit_image":
        pid = temp[uid]["id"]
        image = extract_image_from_message(m)
        if not image:
            return await m.answer("Vui lòng nhập ảnh hoặc URL/file_id ảnh.")
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.image = image
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật phương tiện.", reply_markup=home())

    if state == "auto_edit_button":
        pid = temp[uid]["id"]
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.button = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật nút.", reply_markup=home())

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

    if state == "auto_edit_start":
        pid = temp[uid]["id"]
        if not parse_dt(m.text or ""):
            return await m.answer("Sai format. Dùng: YYYY-MM-DD HH:MM")
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.start_at = m.text.strip()
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật ngày bắt đầu.", reply_markup=home())

    if state == "auto_edit_end":
        pid = temp[uid]["id"]
        if not parse_dt(m.text or ""):
            return await m.answer("Sai format. Dùng: YYYY-MM-DD HH:MM")
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.end_at = m.text.strip()
                await db.commit()
        reset(uid)
        return await m.answer("Đã cập nhật ngày kết thúc.", reply_markup=home())

    # --- KEYWORD AUTO REPLY ---
    text_ = (m.text or "").strip()
    if not text_ or text_.startswith("/"):
        return

    async with SessionLocal() as db:
        kws = (await db.execute(
            select(Keyword).where(Keyword.active == 1).order_by(Keyword.id.desc())
        )).scalars().all()

    if not kws:
        return

    matched = None
    lower = text_.lower()
    for k in kws:
        key = (k.key or "").lower()
        if k.mode == "exact" and lower == key:
            matched = k
            break
        if k.mode == "contains" and key in lower:
            matched = k
            break

    if matched:
        await send_preview(chat_id=m.chat.id, text=matched.text, image=matched.image, button=matched.button)

# ======================
# WELCOME MEMBER
# ======================
@dp.message(F.new_chat_members)
async def welcome_new_member(m: types.Message):
    if not m.chat:
        return
    chat_id = str(m.chat.id)

    async with SessionLocal() as db:
        w = (await db.execute(
            select(WelcomeSetting).where(WelcomeSetting.chat_id == chat_id, WelcomeSetting.active == 1)
        )).scalars().first()

    if not w:
        return

    try:
        msg = await send_preview(chat_id=m.chat.id, text=w.text, image=w.image, button=w.button)

        if w.pin:
            with contextlib.suppress(Exception):
                await bot.pin_chat_message(chat_id=m.chat.id, message_id=msg.message_id)

        if w.delete_after and w.delete_after > 0:
            async def later_delete():
                await asyncio.sleep(w.delete_after * 60)
                with contextlib.suppress(Exception):
                    await bot.delete_message(chat_id=m.chat.id, message_id=msg.message_id)
            asyncio.create_task(later_delete())

    except Exception as e:
        print(f"welcome lỗi: {e}")

# ======================
# AUTO WORKER
# ======================
async def auto_worker():
    while True:
        try:
            now = int(time.time())

            async with SessionLocal() as db:
                posts = (await db.execute(select(AutoPost).where(AutoPost.active == 1))).scalars().all()

            for p in posts:
                if not p.chat_id:
                    continue

                interval_sec = max(int(p.interval or 10), 1) * 60
                if now - int(p.last_sent_ts or 0) < interval_sec:
                    continue

                start_dt = parse_dt(p.start_at) if p.start_at else None
                end_dt = parse_dt(p.end_at) if p.end_at else None

                if start_dt and datetime.now() < start_dt:
                    continue
                if end_dt and datetime.now() > end_dt:
                    continue

                try:
                    msg = await send_preview(chat_id=p.chat_id, text=p.text, image=p.image, button=p.button)

                    async with SessionLocal() as db:
                        row = await db.get(AutoPost, p.id)
                        if row:
                            row.last_sent_ts = now
                            await db.commit()

                    if p.pin:
                        with contextlib.suppress(Exception):
                            await bot.pin_chat_message(chat_id=p.chat_id, message_id=msg.message_id)

                except Exception as e:
                    print(f"auto post {p.id} lỗi: {e}")

        except Exception as e:
            print(f"auto_worker lỗi: {e}")

        await asyncio.sleep(10)

# ======================
# FASTAPI
# ======================
@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = types.Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.on_event("startup")
async def startup():
    global worker_task
    await ensure_schema()
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






