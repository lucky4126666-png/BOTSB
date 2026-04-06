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
from sqlalchemy import Column, Integer, String, Text, select, delete

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN or not BASE_URL or not DATABASE_URL:
    raise RuntimeError("缺少 BOT_TOKEN / BASE_URL / DATABASE_URL")

BASE_URL = BASE_URL.rstrip("/")

OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

worker_task = None

user_state = {}
temp = {}
selected_group = {}
selected_lang = {}
private_menu_msg = {}

TEXTS = {
    "zh": {
        "stranger_text": "欢迎使用机器人，请点击下方按钮：",
        "btn_add_group": "➕ 添加机器人进群",
        "btn_official": "🌐 官方服务",

        "home": "🏠 首页",
        "admin_menu": "👑 管理员设置",
        "group_menu": "👥 群组管理",
        "language": "🌐 语言",
        "back": "⬅️ 返回",

        "choose_group_manage": "👥 请选择要管理的群组：",
        "group_list": "📋 群组列表",
        "no_groups": "暂无群组。",
        "no_saved_groups": "暂无已保存的群组。",

        "kw_title": "📌 关键词",
        "wl_title": "👋 群组欢迎",
        "auto_title": "📅 定时发送",

        "select_group": "➕ 请选择群组：",
        "selected_group": "✅ 已选择群组：",

        "lang_title": "🌐 语言",
        "lang_zh_selected": "✅ 已选择：中文",
        "lang_vi_selected": "✅ 已选择：越南语",

        "kw_list_title": "关键词列表",
        "kw_none": "暂无关键词。",
        "kw_not_found": "关键词不存在。",
        "kw_no_find": "未找到关键词。",
        "kw_details": "关键词详情",
        "kw_key": "关键词",
        "kw_mode": "模式",
        "kw_enabled": "启用",
        "kw_btn": "按钮",
        "kw_exact": "精确匹配",
        "kw_contains": "包含匹配",
        "kw_open": "✅ 开启",
        "kw_close": "❌ 关闭",
        "kw_add_prompt": "请输入关键词。\n如果要一次添加多个关键词，请每行一个。",
        "kw_new_key": "请输入新的关键词：",
        "kw_new_text": "请输入回复文本：",
        "kw_new_img": "请发送图片，或输入图片 URL / file_id：",
        "kw_new_btn": "按钮格式：\nGoogle - https://google.com && YouTube - https://youtube.com\n每一行代表一排按钮。",
        "kw_empty": "关键词不能为空。",
        "kw_exist": "关键词已存在，请输入其他关键词。",
        "kw_added": "已添加 {n} 个关键词。\n已跳过 {m} 个已存在关键词。",
        "kw_updated": "已更新。",

        "wl_list_title": "群组欢迎设置",
        "wl_none": "暂无群组欢迎配置。",
        "wl_not_found": "欢迎配置不存在。",
        "wl_details": "群组欢迎详情",
        "wl_state": "状态",
        "wl_delete_after": "删除消息",
        "wl_no_delete": "不删除",
        "wl_new_chat": "请输入群组 chat_id：",
        "wl_new_text": "请输入欢迎文本：",
        "wl_new_img": "请发送图片，或输入图片 URL / file_id：",
        "wl_new_btn": "按钮格式：\nGoogle - https://google.com && YouTube - https://youtube.com\n每一行代表一排按钮。",
        "wl_del_min": "请输入删除消息的分钟数（0 = 不删除）：",
        "wl_created": "欢迎配置已创建。",
        "wl_updated": "已更新。",
        "wl_exist": "该 chat_id 已存在。",
        "wl_invalid_min": "请输入大于等于 0 的整数。",

        "auto_list_title": "定时发送列表",
        "auto_none": "暂无定时发送。",
        "auto_not_found": "定时发送不存在。",
        "auto_details": "定时发送详情",
        "auto_new_chat": "请输入要创建定时发送的 chat_id：",
        "auto_new_text": "请输入文本内容：",
        "auto_new_img": "请发送图片，或输入图片 URL / file_id：",
        "auto_new_btn": "按钮格式：\nGoogle - https://google.com && YouTube - https://youtube.com\n每一行代表一排按钮。",
        "auto_new_interval": "请输入重复间隔（分钟）：",
        "auto_new_start": "请输入开始时间：YYYY-MM-DD HH:MM",
        "auto_new_end": "请输入结束时间：YYYY-MM-DD HH:MM",
        "auto_created": "定时发送已创建。",
        "auto_updated": "已更新。",
        "auto_invalid_chat": "chat_id 无效。",
        "auto_invalid_interval": "间隔时间必须是正整数。",
        "auto_invalid_dt": "格式错误。请使用：YYYY-MM-DD HH:MM",

        "cancel": "已取消操作。",
        "preview": "👀 预览",
        "list": "📋 列表",
        "add": "➕ 添加",
        "new": "➕ 新建",
    },
    "vi": {
        "stranger_text": "Chào mừng! Chọn một trong các nút bên dưới:",
        "btn_add_group": "➕ Thêm bot vào nhóm",
        "btn_official": "🌐 Dịch vụ chính thức",

        "home": "🏠 Trang chủ",
        "admin_menu": "👑 Cài đặt quản trị",
        "group_menu": "👥 Quản lý nhóm",
        "language": "🌐 Ngôn ngữ",
        "back": "⬅️ Trở lại",

        "choose_group_manage": "👥 Chọn nhóm để quản lý:",
        "group_list": "📋 Danh sách nhóm",
        "no_groups": "Chưa có nhóm nào.",
        "no_saved_groups": "Chưa có nhóm nào được lưu.",

        "kw_title": "📌 Từ khoá",
        "wl_title": "👋 Chào mừng nhóm",
        "auto_title": "📅 Tin nhắn định kỳ",

        "select_group": "➕ Chọn nhóm:",
        "selected_group": "✅ Đã chọn nhóm:",

        "lang_title": "🌐 Ngôn ngữ",
        "lang_zh_selected": "✅ Đã chọn: Tiếng Trung",
        "lang_vi_selected": "✅ Đã chọn: Tiếng Việt",

        "kw_list_title": "Danh sách từ khoá",
        "kw_none": "Chưa có từ khoá nào.",
        "kw_not_found": "Không tìm thấy từ khoá.",
        "kw_no_find": "Không tìm thấy từ khoá.",
        "kw_details": "Chi tiết từ khoá",
        "kw_key": "Từ khoá",
        "kw_mode": "Chế độ",
        "kw_enabled": "Kích hoạt",
        "kw_btn": "Nút",
        "kw_exact": "Khớp chính xác",
        "kw_contains": "Chứa",
        "kw_open": "✅ Bật",
        "kw_close": "❌ Tắt",
        "kw_add_prompt": "Nhập từ khoá.\nNếu muốn thêm nhiều từ khoá, mỗi dòng một từ khoá.",
        "kw_new_key": "Nhập từ khoá mới:",
        "kw_new_text": "Nhập văn bản phản hồi:",
        "kw_new_img": "Gửi ảnh hoặc nhập URL / file_id ảnh:",
        "kw_new_btn": "Định dạng nút:\nGoogle - https://google.com && YouTube - https://youtube.com\nMỗi dòng là một hàng.",
        "kw_empty": "Từ khoá không được để trống.",
        "kw_exist": "Từ khoá đã tồn tại, hãy nhập từ khoá khác.",
        "kw_added": "Đã thêm {n} từ khoá.\nĐã bỏ qua {m} từ khoá đã tồn tại.",
        "kw_updated": "Đã cập nhật.",

        "wl_list_title": "Cấu hình chào mừng nhóm",
        "wl_none": "Chưa có cấu hình chào mừng nào.",
        "wl_not_found": "Không tồn tại cấu hình chào mừng.",
        "wl_details": "Chi tiết chào mừng nhóm",
        "wl_state": "Trạng thái",
        "wl_delete_after": "Xoá tin nhắn",
        "wl_no_delete": "Không xoá",
        "wl_new_chat": "Nhập chat_id của nhóm:",
        "wl_new_text": "Nhập văn bản chào mừng:",
        "wl_new_img": "Gửi ảnh hoặc nhập URL / file_id ảnh:",
        "wl_new_btn": "Định dạng nút:\nGoogle - https://google.com && YouTube - https://youtube.com\nMỗi dòng là một hàng.",
        "wl_del_min": "Nhập số phút để xoá tin nhắn (0 = không xoá):",
        "wl_created": "Đã tạo cấu hình chào mừng.",
        "wl_updated": "Đã cập nhật.",
        "wl_exist": "Chat ID này đã tồn tại.",
        "wl_invalid_min": "Vui lòng nhập số nguyên >= 0.",

        "auto_list_title": "Danh sách tin nhắn định kỳ",
        "auto_none": "Chưa có auto post nào.",
        "auto_not_found": "Không tồn tại tin nhắn định kỳ.",
        "auto_details": "Chi tiết tin nhắn định kỳ",
        "auto_new_chat": "Nhập chat_id để tạo auto post:",
        "auto_new_text": "Nhập nội dung văn bản:",
        "auto_new_img": "Gửi ảnh hoặc nhập URL / file_id ảnh:",
        "auto_new_btn": "Định dạng nút:\nGoogle - https://google.com && YouTube - https://youtube.com\nMỗi dòng là một hàng.",
        "auto_new_interval": "Nhập khoảng thời gian lặp lại (phút):",
        "auto_new_start": "Nhập thời gian bắt đầu: YYYY-MM-DD HH:MM",
        "auto_new_end": "Nhập thời gian kết thúc: YYYY-MM-DD HH:MM",
        "auto_created": "Đã tạo auto post.",
        "auto_updated": "Đã cập nhật.",
        "auto_invalid_chat": "chat_id không hợp lệ.",
        "auto_invalid_interval": "Khoảng thời gian phải là số nguyên dương.",
        "auto_invalid_dt": "Sai định dạng. Dùng: YYYY-MM-DD HH:MM",

        "cancel": "Đã huỷ thao tác.",
        "preview": "👀 Xem trước",
        "list": "📋 Danh sách",
        "add": "➕ Thêm",
        "new": "➕ Tạo",
    }
}


def get_lang(uid: int) -> str:
    return selected_lang.get(uid, "zh")


def t(uid: int, key: str) -> str:
    lang = get_lang(uid)
    return TEXTS.get(lang, TEXTS["zh"]).get(key, key)


def is_allowed_user(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in ADMIN_IDS


def can_change_language(user_id: int) -> bool:
    return is_allowed_user(user_id)


def reset(uid):
    user_state.pop(uid, None)
    temp.pop(uid, None)


async def ack(c: types.CallbackQuery, text: str | None = None):
    with contextlib.suppress(Exception):
        await c.answer(text=text)


def stranger_start_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ 添加机器人进群", url="https://t.me/nnnnzubot?startgroup=foo")],
        [InlineKeyboardButton(text="🌐 官方服务", url="https://t.me/xbkf/")]
    ])


async def allowed_or_ignore(c: types.CallbackQuery):
    if not c.from_user or not is_allowed_user(c.from_user.id):
        await ack(c)
        return False
    return True


# ======================
# BUTTON PARSER
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
                title, url = part.split(" - ", 1)
            elif "-" in part:
                title, url = part.split("-", 1)
            else:
                continue
            row.append({"text": title.strip(), "url": url.strip()})
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


async def safe_edit(message, text_: str, reply_markup=None):
    if not message:
        return None
    try:
        return await message.edit_text(text_, reply_markup=reply_markup)
    except TelegramBadRequest:
        return await message.answer(text_, reply_markup=reply_markup)
    except Exception:
        return await message.answer(text_, reply_markup=reply_markup)


async def send_preview(chat_id, text=None, image=None, button=None):
    kb = build_buttons(parse_buttons(button))
    try:
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
    except Exception as e:
        print(f"[SEND_PREVIEW ERROR] chat_id={chat_id} error={e}")
        raise


def extract_image_from_message(m: types.Message):
    if m.photo:
        return m.photo[-1].file_id
    if m.text:
        return m.text.strip()
    if m.caption:
        return m.caption.strip()
    return None


def parse_dt(s: str):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M")
    except Exception:
        return None


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
    delete_after = Column(Integer, default=0)  # minutes
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


class BotGroup(Base):
    __tablename__ = "bot_groups"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, unique=True, index=True)
    title = Column(String, default="")
    type = Column(String, default="group")
    is_admin = Column(Integer, default=0)
    updated_at = Column(Integer, default=0)


# ======================
# MENUS
# ======================
def start_menu_kb(uid: int | None = None):
    uid = uid or 0
    kb = [
        [InlineKeyboardButton(text=t(uid, "admin_menu"), callback_data="admin_menu")],
        [InlineKeyboardButton(text=t(uid, "group_menu"), callback_data="group_menu")],
    ]
    if uid and can_change_language(uid):
        kb.append([InlineKeyboardButton(text=t(uid, "language"), callback_data="lang_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_menu_kb(uid: int | None = None):
    uid = uid or 0
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(uid, "kw_title"), callback_data="kw_menu")],
        [InlineKeyboardButton(text=t(uid, "wl_title"), callback_data="wl_menu")],
        [InlineKeyboardButton(text=t(uid, "auto_title"), callback_data="auto_menu")],
        [InlineKeyboardButton(text=t(uid, "language"), callback_data="lang_menu")],
        [InlineKeyboardButton(text=t(uid, "back"), callback_data="back_start")],
    ])


def group_menu_kb(uid: int | None = None):
    uid = uid or 0
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(uid, "group_list"), callback_data="group_list")],
        [InlineKeyboardButton(text=t(uid, "select_group"), callback_data="group_pick")],
        [InlineKeyboardButton(text=t(uid, "back"), callback_data="back_start")],
    ])


def lang_menu_kb(uid: int | None = None):
    uid = uid or 0
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇻🇳 越南语", callback_data="lang_vi")],
        [InlineKeyboardButton(text="🇨🇳 中文", callback_data="lang_zh")],
        [InlineKeyboardButton(text=t(uid, "back"), callback_data="back_start")],
    ])


def group_select_kb(groups, uid: int | None = None):
    uid = uid or 0
    kb = []
    for g in groups:
        title = g.title or g.chat_id
        admin_mark = " 👑" if g.is_admin else ""
        kb.append([
            InlineKeyboardButton(
                text=f"{title}{admin_mark}",
                callback_data=f"pick_group_{g.chat_id}"
            )
        ])
    kb.append([InlineKeyboardButton(text=t(uid, "back"), callback_data="back_start")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def kw_menu_kb(uid: int | None = None):
    uid = uid or 0
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(uid, "add"), callback_data="kw_add")],
        [InlineKeyboardButton(text=t(uid, "list"), callback_data="kw_list")],
        [InlineKeyboardButton(text=t(uid, "back"), callback_data="back_start")],
    ])


def wl_menu_kb(uid: int | None = None):
    uid = uid or 0
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(uid, "new"), callback_data="wl_add")],
        [InlineKeyboardButton(text=t(uid, "list"), callback_data="wl_list")],
        [InlineKeyboardButton(text=t(uid, "back"), callback_data="back_start")],
    ])


def auto_menu_kb(uid: int | None = None):
    uid = uid or 0
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(uid, "new"), callback_data="auto_add")],
        [InlineKeyboardButton(text=t(uid, "list"), callback_data="auto_list")],
        [InlineKeyboardButton(text=t(uid, "back"), callback_data="back_start")],
    ])


# ======================
# HELPERS
# ======================
async def get_admin_groups():
    async with SessionLocal() as db:
        groups = (await db.execute(
            select(BotGroup).where(BotGroup.is_admin == 1).order_by(BotGroup.id.desc())
        )).scalars().all()
    return groups


async def get_all_groups():
    async with SessionLocal() as db:
        groups = (await db.execute(
            select(BotGroup).order_by(BotGroup.id.desc())
        )).scalars().all()
    return groups


async def show_kw_list(message, uid: int):
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
        t(uid, "kw_list_title") if rows else t(uid, "kw_none"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


async def show_kw_view(message, kid, uid: int):
    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)
    if not k:
        return await message.answer(t(uid, "kw_not_found"))

    await safe_edit(
        message,
        f"{t(uid, 'kw_details')}\n\n"
        f"{t(uid, 'kw_key')}: {k.key}\n"
        f"{t(uid, 'kw_mode')}: {'精确匹配' if k.mode == 'exact' else '包含匹配'}\n"
        f"{t(uid, 'kw_enabled')}: {'✅' if k.active else '❌'}\n"
        f"{t(uid, 'kw_btn')}: {'✅' if k.button else '❌'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"模式：{'✅ 精确匹配' if k.mode == 'exact' else '包含匹配'}", callback_data=f"kw_mode_{k.id}")],
            [InlineKeyboardButton(text=f"状态：{'✅ 开启' if k.active else '❌ 关闭'}", callback_data=f"kw_toggle_{k.id}")],
            [InlineKeyboardButton(text="📝 修改关键词", callback_data=f"kw_key_{k.id}")],
            [InlineKeyboardButton(text="📝 修改文本", callback_data=f"kw_text_{k.id}")],
            [InlineKeyboardButton(text="🖼 修改媒体", callback_data=f"kw_img_{k.id}")],
            [InlineKeyboardButton(text="🔤 修改按钮", callback_data=f"kw_btn_{k.id}")],
            [InlineKeyboardButton(text=t(uid, "preview"), callback_data=f"kw_pre_{k.id}")],
            [InlineKeyboardButton(text="⬅️ 返回", callback_data="kw_list")],
        ])
    )


async def show_wl_list(message, uid: int):
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
        t(uid, "wl_list_title") if rows else t(uid, "wl_none"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


async def show_wl_view(message, wid, uid: int):
    async with SessionLocal() as db:
        w = await db.get(WelcomeSetting, wid)
    if not w:
        return await message.answer(t(uid, "wl_not_found"))

    await safe_edit(
        message,
        f"{t(uid, 'wl_details')}\n\n"
        f"{t(uid, 'wl_state')}: {'开启' if w.active else '关闭'}\n"
        f"{t(uid, 'wl_delete_after')}: {w.delete_after if w.delete_after else t(uid, 'wl_no_delete')}\n"
        f"图片: {'✅' if w.image else '❌'}\n"
        f"按钮: {'✅' if w.button else '❌'}\n"
        f"文本: {'✅' if w.text else '❌'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"状态：{'✅ 开启' if w.active else '❌ 关闭'}", callback_data=f"wl_toggle_{w.id}")],
            [InlineKeyboardButton(text=f"{t(uid, 'wl_delete_after')}：{w.delete_after if w.delete_after else 0} 分钟", callback_data=f"wl_delmin_{w.id}")],
            [InlineKeyboardButton(text="📌 置顶", callback_data=f"wl_pin_{w.id}")],
            [InlineKeyboardButton(text="📝 修改文本", callback_data=f"wl_text_{w.id}")],
            [InlineKeyboardButton(text="📷 修改媒体", callback_data=f"wl_img_{w.id}")],
            [InlineKeyboardButton(text="🔤 修改按钮", callback_data=f"wl_btn_{w.id}")],
            [InlineKeyboardButton(text=t(uid, "preview"), callback_data=f"wl_pre_{w.id}")],
            [InlineKeyboardButton(text="⬅️ 返回", callback_data="wl_list")],
        ])
    )


async def show_auto_list(message, uid: int):
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
        t(uid, "auto_list_title") if rows else t(uid, "auto_none"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


async def show_auto_view(message, pid, uid: int):
    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)
    if not p:
        return await message.answer(t(uid, "auto_not_found"))

    last_time = "-"
    if p.last_sent_ts:
        last_time = datetime.fromtimestamp(p.last_sent_ts).strftime("%Y-%m-%d %H:%M:%S")

    await safe_edit(
        message,
        f"{t(uid, 'auto_details')}\n\n"
        f"状态：{'✅ 开启' if p.active else '❌ 关闭'}\n"
        f"间隔：{p.interval} 分钟\n"
        f"开始：{p.start_at or '-'}\n"
        f"结束：{p.end_at or '-'}\n"
        f"最近发送：{last_time}\n\n"
        f"图片：{'✅' if p.image else '❌'}\n"
        f"按钮：{'✅' if p.button else '❌'}\n"
        f"文本：{'✅' if p.text else '❌'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"状态：{'✅ 开启' if p.active else '❌ 关闭'}", callback_data=f"auto_toggle_{p.id}")],
            [InlineKeyboardButton(text=f"📌 置顶：{'✅ 是' if p.pin else '❌ 否'}", callback_data=f"auto_pin_{p.id}")],
            [InlineKeyboardButton(text="📝 修改文本", callback_data=f"auto_text_{p.id}")],
            [InlineKeyboardButton(text="📷 修改媒体", callback_data=f"auto_img_{p.id}")],
            [InlineKeyboardButton(text="🔤 修改按钮", callback_data=f"auto_btn_{p.id}")],
            [InlineKeyboardButton(text=t(uid, "preview"), callback_data=f"auto_pre_{p.id}")],
            [InlineKeyboardButton(text="⏩ 间隔时间", callback_data=f"auto_int_{p.id}")],
            [InlineKeyboardButton(text="📅 开始时间", callback_data=f"auto_start_{p.id}")],
            [InlineKeyboardButton(text="📅 结束时间", callback_data=f"auto_end_{p.id}")],
            [InlineKeyboardButton(text="⬅️ 返回", callback_data="auto_list")],
        ])
    )


# ======================
# TRACK BOT IN GROUP
# ======================
@dp.my_chat_member()
async def track_bot_membership(event: types.ChatMemberUpdated):
    chat = event.chat
    new_status = event.new_chat_member.status

    if chat.type not in ("group", "supergroup"):
        return

    chat_id = str(chat.id)
    now_ts = int(time.time())

    if new_status in ("member", "administrator"):
        async with SessionLocal() as db:
            row = (await db.execute(
                select(BotGroup).where(BotGroup.chat_id == chat_id)
            )).scalars().first()

            if not row:
                row = BotGroup(
                    chat_id=chat_id,
                    title=chat.title or "",
                    type=chat.type,
                    is_admin=1 if new_status == "administrator" else 0,
                    updated_at=now_ts
                )
                db.add(row)
            else:
                row.title = chat.title or row.title
                row.type = chat.type
                row.is_admin = 1 if new_status == "administrator" else 0
                row.updated_at = now_ts

            await db.commit()

    if new_status in ("left", "kicked"):
        async with SessionLocal() as db:
            await db.execute(delete(BotGroup).where(BotGroup.chat_id == chat_id))
            await db.commit()


# ======================
# START / HOME
# ======================
@dp.message(F.text.startswith("/start"))
async def start(m: types.Message):
    print("[START HIT]", m.from_user.id, m.text)
    await m.answer("Bot đang hoạt động.")
    if not m.from_user:
        return

    uid = m.from_user.id
    print(f"[START] uid={uid} text={m.text!r}")

    # Người lạ: chỉ 1 tin + 2 nút
    if not is_allowed_user(uid):
        await m.answer(
            t(uid, "stranger_text"),
            reply_markup=stranger_start_kb()
        )
        return

    reset(uid)

    # Private chat
    if m.chat.type == "private":
        old_msg_id = private_menu_msg.get(uid)

        if old_msg_id:
            with contextlib.suppress(Exception):
                await bot.edit_message_text(
                    chat_id=m.chat.id,
                    message_id=old_msg_id,
                    text=t(uid, "home"),
                    reply_markup=start_menu_kb(uid)
                )
            return

        msg = await m.answer(t(uid, "home"), reply_markup=start_menu_kb(uid))
        private_menu_msg[uid] = msg.message_id
        return

    # Group
    groups = await get_admin_groups()
    if groups:
        await m.answer(t(uid, "choose_group_manage"), reply_markup=group_select_kb(groups, uid))
        return

    await m.answer(t(uid, "home"), reply_markup=start_menu_kb(uid))


@dp.message(F.text == "/cancel")
async def cancel(m: types.Message):
    if not m.from_user:
        return
    uid = m.from_user.id
    reset(uid)
    await m.answer(t(uid, "cancel"), reply_markup=start_menu_kb(uid))


@dp.callback_query(F.data == "back_start")
async def back_start(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await safe_edit(c.message, t(uid, "home"), reply_markup=start_menu_kb(uid))


# ======================
# MAIN MENU
# ======================
@dp.callback_query(F.data == "admin_menu")
async def admin_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await safe_edit(c.message, t(uid, "admin_menu"), reply_markup=admin_menu_kb(uid))


@dp.callback_query(F.data == "group_menu")
async def group_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await safe_edit(c.message, t(uid, "group_menu"), reply_markup=group_menu_kb(uid))


@dp.callback_query(F.data == "lang_menu")
async def lang_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await safe_edit(c.message, t(uid, "lang_title"), reply_markup=lang_menu_kb(uid))


@dp.callback_query(F.data == "group_list")
async def group_list(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    groups = await get_all_groups()

    kb = []
    for g in groups:
        kb.append([
            InlineKeyboardButton(
                text=f"{g.title or g.chat_id} ({'管理员' if g.is_admin else '成员'})",
                callback_data=f"pick_group_{g.chat_id}"
            )
        ])
    kb.append([InlineKeyboardButton(text=t(uid, "back"), callback_data="group_menu")])

    await safe_edit(
        c.message,
        t(uid, "group_list") if groups else t(uid, "no_saved_groups"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


@dp.callback_query(F.data == "group_pick")
async def group_pick(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    groups = await get_all_groups()
    if not groups:
        return await c.message.answer(t(uid, "no_groups"))
    await safe_edit(c.message, t(uid, "select_group"), reply_markup=group_select_kb(groups, uid))


@dp.callback_query(F.data.startswith("pick_group_"))
async def pick_group(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    chat_id = c.data.replace("pick_group_", "")
    selected_group[uid] = chat_id

    async with SessionLocal() as db:
        g = (await db.execute(
            select(BotGroup).where(BotGroup.chat_id == chat_id)
        )).scalars().first()

    title = g.title if g and g.title else chat_id

    await safe_edit(
        c.message,
        f"{t(uid, 'selected_group')}\n{title}\n\n{t(uid, 'home')}",
        reply_markup=start_menu_kb(uid)
    )


@dp.callback_query(F.data == "lang_vi")
async def lang_vi(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    selected_lang[uid] = "vi"
    await safe_edit(c.message, t(uid, "lang_vi_selected"), reply_markup=start_menu_kb(uid))


@dp.callback_query(F.data == "lang_zh")
async def lang_zh(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    selected_lang[uid] = "zh"
    await safe_edit(c.message, t(uid, "lang_zh_selected"), reply_markup=start_menu_kb(uid))


# ======================
# KEYWORD MENU
# ======================
@dp.callback_query(F.data == "kw_menu")
async def kw_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await safe_edit(c.message, t(uid, "kw_title"), reply_markup=kw_menu_kb(uid))


@dp.callback_query(F.data == "kw_add")
async def kw_add(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    user_state[uid] = "kw_add_key"
    temp[uid] = {}
    await c.message.answer(t(uid, "kw_add_prompt"))


@dp.callback_query(F.data == "kw_list")
async def kw_list(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await show_kw_list(c.message, uid)


@dp.callback_query(F.data.startswith("kw_view_"))
async def kw_view(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await show_kw_view(c.message, int(c.data.split("_")[-1]), uid)


@dp.callback_query(F.data.startswith("kw_toggle_"))
async def kw_toggle(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    kid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)
        if not k:
            return await c.message.answer(t(uid, "kw_no_find"))
        k.active = 0 if k.active else 1
        await db.commit()
    await show_kw_view(c.message, kid, uid)


@dp.callback_query(F.data.startswith("kw_mode_"))
async def kw_mode(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    kid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)
        if not k:
            return await c.message.answer(t(uid, "kw_no_find"))
        k.mode = "contains" if k.mode == "exact" else "exact"
        await db.commit()
    await show_kw_view(c.message, kid, uid)


@dp.callback_query(F.data.startswith("kw_key_"))
async def kw_key(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    kid = int(c.data.split("_")[-1])
    user_state[uid] = "kw_edit_key"
    temp[uid] = {"id": kid}
    await c.message.answer(t(uid, "kw_new_key"))


@dp.callback_query(F.data.startswith("kw_text_"))
async def kw_text(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    kid = int(c.data.split("_")[-1])
    user_state[uid] = "kw_edit_text"
    temp[uid] = {"id": kid}
    await c.message.answer(t(uid, "kw_new_text"))


@dp.callback_query(F.data.startswith("kw_img_"))
async def kw_img(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    kid = int(c.data.split("_")[-1])
    user_state[uid] = "kw_edit_image"
    temp[uid] = {"id": kid}
    await c.message.answer(t(uid, "kw_new_img"))


@dp.callback_query(F.data.startswith("kw_btn_"))
async def kw_btn(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    kid = int(c.data.split("_")[-1])
    user_state[uid] = "kw_edit_button"
    temp[uid] = {"id": kid}
    await c.message.answer(t(uid, "kw_new_btn"))


@dp.callback_query(F.data.startswith("kw_pre_"))
async def kw_pre(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    kid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)
    if not k:
        return await c.message.answer(t(uid, "kw_not_found"))
    await send_preview(chat_id=c.from_user.id, text=k.text, image=k.image, button=k.button)


@dp.callback_query(F.data.startswith("kw_del_"))
async def kw_del(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    kid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        await db.execute(delete(Keyword).where(Keyword.id == kid))
        await db.commit()
    await ack(c, "已删除")
    await show_kw_list(c.message, uid)


# ======================
# WELCOME MENU
# ======================
@dp.callback_query(F.data == "wl_menu")
async def wl_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await safe_edit(c.message, t(uid, "wl_title"), reply_markup=wl_menu_kb(uid))


@dp.callback_query(F.data == "wl_add")
async def wl_add(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    user_state[uid] = "wl_add_chat"
    temp[uid] = {}
    await c.message.answer(t(uid, "wl_new_chat"))


@dp.callback_query(F.data == "wl_list")
async def wl_list(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await show_wl_list(c.message, uid)


@dp.callback_query(F.data.startswith("wl_view_"))
async def wl_view(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await show_wl_view(c.message, int(c.data.split("_")[-1]), uid)


@dp.callback_query(F.data.startswith("wl_toggle_"))
async def wl_toggle(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    wid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        w = await db.get(WelcomeSetting, wid)
        if not w:
            return await c.message.answer(t(uid, "wl_not_found"))
        w.active = 0 if w.active else 1
        await db.commit()
    await show_wl_view(c.message, wid, uid)


@dp.callback_query(F.data.startswith("wl_text_"))
async def wl_text(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    wid = int(c.data.split("_")[-1])
    user_state[uid] = "wl_edit_text"
    temp[uid] = {"id": wid}
    await c.message.answer(t(uid, "wl_new_text"))


@dp.callback_query(F.data.startswith("wl_img_"))
async def wl_img(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    wid = int(c.data.split("_")[-1])
    user_state[uid] = "wl_edit_image"
    temp[uid] = {"id": wid}
    await c.message.answer(t(uid, "wl_new_img"))


@dp.callback_query(F.data.startswith("wl_btn_"))
async def wl_btn(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    wid = int(c.data.split("_")[-1])
    user_state[uid] = "wl_edit_button"
    temp[uid] = {"id": wid}
    await c.message.answer(t(uid, "wl_new_btn"))


@dp.callback_query(F.data.startswith("wl_delmin_"))
async def wl_delmin(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    wid = int(c.data.split("_")[-1])
    user_state[uid] = "wl_edit_delete_after"
    temp[uid] = {"id": wid}
    await c.message.answer(t(uid, "wl_del_min"))


@dp.callback_query(F.data.startswith("wl_pre_"))
async def wl_pre(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    wid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        w = await db.get(WelcomeSetting, wid)
    if not w:
        return await c.message.answer(t(uid, "wl_not_found"))
    await send_preview(chat_id=c.from_user.id, text=w.text, image=w.image, button=w.button)


@dp.callback_query(F.data.startswith("wl_del_"))
async def wl_del(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    wid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        await db.execute(delete(WelcomeSetting).where(WelcomeSetting.id == wid))
        await db.commit()
    await ack(c, "已删除")
    await show_wl_list(c.message, uid)


@dp.callback_query(F.data.startswith("wl_pin_"))
async def wl_pin(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    wid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        w = await db.get(WelcomeSetting, wid)
        if not w:
            return await c.message.answer(t(uid, "wl_not_found"))
        w.pin = 0 if w.pin else 1
        await db.commit()
    await show_wl_view(c.message, wid, uid)


# ======================
# AUTO MENU
# ======================
@dp.callback_query(F.data == "auto_menu")
async def auto_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await safe_edit(c.message, t(uid, "auto_title"), reply_markup=auto_menu_kb(uid))


@dp.callback_query(F.data == "auto_add")
async def auto_add(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    user_state[uid] = "auto_add_chat"
    temp[uid] = {}
    await c.message.answer(t(uid, "auto_new_chat"))


@dp.callback_query(F.data == "auto_list")
async def auto_list(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await show_auto_list(c.message, uid)


@dp.callback_query(F.data.startswith("auto_view_"))
async def auto_view(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await show_auto_view(c.message, int(c.data.split("_")[-1]), uid)


@dp.callback_query(F.data.startswith("auto_toggle_"))
async def auto_toggle(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    pid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)
        if not p:
            return await c.message.answer(t(uid, "auto_not_found"))
        p.active = 0 if p.active else 1
        await db.commit()
    await show_auto_view(c.message, pid, uid)


@dp.callback_query(F.data.startswith("auto_pin_"))
async def auto_pin(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    pid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)
        if not p:
            return await c.message.answer(t(uid, "auto_not_found"))
        p.pin = 0 if p.pin else 1
        await db.commit()
    await show_auto_view(c.message, pid, uid)


@dp.callback_query(F.data.startswith("auto_text_"))
async def auto_text(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    pid = int(c.data.split("_")[-1])
    user_state[uid] = "auto_edit_text"
    temp[uid] = {"id": pid}
    await c.message.answer(t(uid, "auto_new_text"))


@dp.callback_query(F.data.startswith("auto_img_"))
async def auto_img(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    pid = int(c.data.split("_")[-1])
    user_state[uid] = "auto_edit_image"
    temp[uid] = {"id": pid}
    await c.message.answer(t(uid, "auto_new_img"))


@dp.callback_query(F.data.startswith("auto_btn_"))
async def auto_btn(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    pid = int(c.data.split("_")[-1])
    user_state[uid] = "auto_edit_button"
    temp[uid] = {"id": pid}
    await c.message.answer(t(uid, "auto_new_btn"))


@dp.callback_query(F.data.startswith("auto_chat_"))
async def auto_chat(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    pid = int(c.data.split("_")[-1])
    user_state[uid] = "auto_edit_chat"
    temp[uid] = {"id": pid}
    await c.message.answer("请输入新的 chat_id：" if get_lang(uid) == "zh" else "Nhập chat_id mới:")


@dp.callback_query(F.data.startswith("auto_int_"))
async def auto_int(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    pid = int(c.data.split("_")[-1])
    user_state[uid] = "auto_edit_interval"
    temp[uid] = {"id": pid}
    await c.message.answer(t(uid, "auto_new_interval"))


@dp.callback_query(F.data.startswith("auto_start_"))
async def auto_start(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    pid = int(c.data.split("_")[-1])
    user_state[uid] = "auto_edit_start"
    temp[uid] = {"id": pid}
    await c.message.answer(t(uid, "auto_new_start"))


@dp.callback_query(F.data.startswith("auto_end_"))
async def auto_end(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    pid = int(c.data.split("_")[-1])
    user_state[uid] = "auto_edit_end"
    temp[uid] = {"id": pid}
    await c.message.answer(t(uid, "auto_new_end"))


@dp.callback_query(F.data.startswith("auto_pre_"))
async def auto_pre(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    pid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)
    if not p:
        return await c.message.answer(t(uid, "auto_not_found"))
    await send_preview(chat_id=c.from_user.id, text=p.text, image=p.image, button=p.button)


@dp.callback_query(F.data.startswith("auto_del_"))
async def auto_del(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    pid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        await db.execute(delete(AutoPost).where(AutoPost.id == pid))
        await db.commit()
    await ack(c, "已删除")
    await show_auto_list(c.message, uid)


# ======================
# STATE HANDLER
# ======================
@dp.message()
async def all_messages(m: types.Message):
    if not m.from_user:
        return

    uid = m.from_user.id
    state = user_state.get(uid)

    # Người lạ: không xử lý gì thêm
    if not is_allowed_user(uid):
        return

    print(f"[MESSAGE] chat={m.chat.id} user={uid} text={m.text!r} state={state}")

    # ---- KEYWORD ----
    if state == "kw_add_key":
        raw = (m.text or "").strip()
        if not raw:
            return await m.answer(t(uid, "kw_empty"))

        keys = [line.strip() for line in raw.splitlines() if line.strip()]
        if not keys:
            return await m.answer(t(uid, "kw_empty"))

        added = 0
        existed = 0

        async with SessionLocal() as db:
            for key in keys:
                exists = (await db.execute(select(Keyword).where(Keyword.key == key))).scalars().first()
                if exists:
                    existed += 1
                    continue
                db.add(Keyword(key=key, mode="exact", active=1, text="", image="", button=""))
                added += 1
            await db.commit()

        reset(uid)
        return await m.answer(
            t(uid, "kw_added").format(n=added, m=existed),
            reply_markup=start_menu_kb(uid)
        )

    if state == "kw_edit_key":
        kid = temp[uid]["id"]
        key = (m.text or "").strip()
        if not key:
            return await m.answer(t(uid, "kw_empty"))

        async with SessionLocal() as db:
            exists = (await db.execute(
                select(Keyword).where(Keyword.key == key, Keyword.id != kid)
            )).scalars().first()
            if exists:
                return await m.answer(t(uid, "kw_exist"))

            k = await db.get(Keyword, kid)
            if k:
                k.key = key
                await db.commit()

        reset(uid)
        return await m.answer(t(uid, "kw_updated"), reply_markup=start_menu_kb(uid))

    if state == "kw_edit_text":
        kid = temp[uid]["id"]
        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if k:
                k.text = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "kw_updated"), reply_markup=start_menu_kb(uid))

    if state == "kw_edit_image":
        kid = temp[uid]["id"]
        image = extract_image_from_message(m)
        if not image:
            return await m.answer(t(uid, "kw_new_img"))
        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if k:
                k.image = image
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "kw_updated"), reply_markup=start_menu_kb(uid))

    if state == "kw_edit_button":
        kid = temp[uid]["id"]
        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if k:
                k.button = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "kw_updated"), reply_markup=start_menu_kb(uid))

    # ---- WELCOME ----
    if state == "wl_add_chat":
        chat_id = (m.text or "").strip()
        if not chat_id:
            return await m.answer(t(uid, "wl_new_chat"))
        async with SessionLocal() as db:
            exists = (await db.execute(select(WelcomeSetting).where(WelcomeSetting.chat_id == chat_id))).scalars().first()
            if exists:
                return await m.answer(t(uid, "wl_exist"))
            db.add(WelcomeSetting(chat_id=chat_id))
            await db.commit()
        reset(uid)
        return await m.answer(t(uid, "wl_created"), reply_markup=start_menu_kb(uid))

    if state == "wl_edit_text":
        wid = temp[uid]["id"]
        async with SessionLocal() as db:
            w = await db.get(WelcomeSetting, wid)
            if w:
                w.text = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "wl_updated"), reply_markup=start_menu_kb(uid))

    if state == "wl_edit_image":
        wid = temp[uid]["id"]
        image = extract_image_from_message(m)
        if not image:
            return await m.answer(t(uid, "wl_new_img"))
        async with SessionLocal() as db:
            w = await db.get(WelcomeSetting, wid)
            if w:
                w.image = image
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "wl_updated"), reply_markup=start_menu_kb(uid))

    if state == "wl_edit_button":
        wid = temp[uid]["id"]
        async with SessionLocal() as db:
            w = await db.get(WelcomeSetting, wid)
            if w:
                w.button = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "wl_updated"), reply_markup=start_menu_kb(uid))

    if state == "wl_edit_delete_after":
        wid = temp[uid]["id"]
        try:
            minutes = int((m.text or "").strip())
            if minutes < 0:
                raise ValueError
        except ValueError:
            return await m.answer(t(uid, "wl_invalid_min"))
        async with SessionLocal() as db:
            w = await db.get(WelcomeSetting, wid)
            if w:
                w.delete_after = minutes
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "wl_updated"), reply_markup=start_menu_kb(uid))

    # ---- AUTO ----
    if state == "auto_add_chat":
        chat_id = (m.text or "").strip()
        if not chat_id:
            return await m.answer(t(uid, "auto_new_chat"))
        async with SessionLocal() as db:
            db.add(AutoPost(chat_id=chat_id))
            await db.commit()
        reset(uid)
        return await m.answer(t(uid, "auto_created"), reply_markup=start_menu_kb(uid))

    if state == "auto_edit_text":
        pid = temp[uid]["id"]
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.text = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "auto_updated"), reply_markup=start_menu_kb(uid))

    if state == "auto_edit_image":
        pid = temp[uid]["id"]
        image = extract_image_from_message(m)
        if not image:
            return await m.answer(t(uid, "auto_new_img"))
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.image = image
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "auto_updated"), reply_markup=start_menu_kb(uid))

    if state == "auto_edit_button":
        pid = temp[uid]["id"]
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.button = m.text or ""
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "auto_updated"), reply_markup=start_menu_kb(uid))

    if state == "auto_edit_chat":
        pid = temp[uid]["id"]
        chat_id = (m.text or "").strip()
        if not chat_id:
            return await m.answer(t(uid, "auto_invalid_chat"))
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.chat_id = chat_id
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "auto_updated"), reply_markup=start_menu_kb(uid))

    if state == "auto_edit_interval":
        pid = temp[uid]["id"]
        try:
            interval = int((m.text or "").strip())
            if interval <= 0:
                raise ValueError
        except ValueError:
            return await m.answer(t(uid, "auto_invalid_interval"))
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.interval = interval
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "auto_updated"), reply_markup=start_menu_kb(uid))

    if state == "auto_edit_start":
        pid = temp[uid]["id"]
        if not parse_dt(m.text or ""):
            return await m.answer(t(uid, "auto_invalid_dt"))
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.start_at = m.text.strip()
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "auto_updated"), reply_markup=start_menu_kb(uid))

    if state == "auto_edit_end":
        pid = temp[uid]["id"]
        if not parse_dt(m.text or ""):
            return await m.answer(t(uid, "auto_invalid_dt"))
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.end_at = m.text.strip()
                await db.commit()
        reset(uid)
        return await m.answer(t(uid, "auto_updated"), reply_markup=start_menu_kb(uid))

    # ---- KEYWORD AUTO REPLY ----
    text_ = (m.text or m.caption or "").strip()
    if not text_ or text_.startswith("/"):
        return

    async with SessionLocal() as db:
        kws = (await db.execute(
            select(Keyword).where(Keyword.active == 1).order_by(Keyword.id.desc())
        )).scalars().all()

    if not kws:
        return

    lower_text = text_.lower()
    matched = None

    for k in kws:
        key = (k.key or "").strip().lower()
        if not key:
            continue

        if k.mode == "exact" and lower_text == key:
            matched = k
            break

        if k.mode == "contains" and key in lower_text:
            matched = k
            break

    if matched:
        print(f"[KW MATCH] {matched.key}")
        await send_preview(
            chat_id=m.chat.id,
            text=matched.text,
            image=matched.image,
            button=matched.button
        )
    else:
        print("[KW NO MATCH]")


# ======================
# WELCOME NEW MEMBER
# ======================
@dp.message(F.new_chat_members)
async def welcome_new_member(m: types.Message):
    if not m.chat:
        return

    chat_id = str(m.chat.id)

    async with SessionLocal() as db:
        w = (await db.execute(
            select(WelcomeSetting).where(
                WelcomeSetting.chat_id == chat_id,
                WelcomeSetting.active == 1
            )
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
        print(f"welcome 错误: {e}")


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
                    print(f"auto post {p.id} 错误: {e}")

        except Exception as e:
            print(f"auto_worker 错误: {e}")

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
    print("[WEBHOOK RAW]", data)

    update = types.Update.model_validate(data)
    print("[WEBHOOK TYPE]", update.event_type)

    await dp.feed_update(bot, update)
    return {"ok": True}


async def ensure_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@app.on_event("startup")
async def startup():
    global worker_task

    await ensure_schema()
    worker_task = asyncio.create_task(auto_worker())

    webhook_url = f"{BASE_URL}/webhook"
    print("[STARTUP] BASE_URL =", BASE_URL)
    print("[STARTUP] webhook_url =", webhook_url)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("[STARTUP] old webhook deleted")

        result = await bot.set_webhook(
            webhook_url,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query", "my_chat_member"]
        )
        print("[STARTUP] set_webhook result =", result)

        info = await bot.get_webhook_info()
        print("[STARTUP] webhook info =", info.model_dump())

        if not info.url:
            print("[ERROR] Webhook chưa được set. Hãy kiểm tra BASE_URL hoặc URL HTTPS public.")

    except Exception as e:
        print("[STARTUP ERROR] set webhook failed:", e)
        raise

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
