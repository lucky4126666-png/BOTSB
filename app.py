import os
import time
import json
import random
import asyncio
import contextlib
import traceback
from html import escape
from datetime import datetime
from typing import Optional, Union
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatPermissions,
)
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, String, Text, select, delete, text

from dotenv import load_dotenv
from openai import AsyncOpenAI
import redis.asyncio as redis

load_dotenv()

# ======================
# ENV
# ======================

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "")

if not BOT_TOKEN or not BASE_URL or not DATABASE_URL:
    raise RuntimeError("缺少 BOT_TOKEN / BASE_URL / DATABASE_URL")

BASE_URL = BASE_URL.rstrip("/")


def normalize_database_url(url: str):
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))

    connect_args = {
        "timeout": 60,
    }

    sslmode = query.pop("sslmode", None)
    if sslmode == "require":
        connect_args["ssl"] = "require"

    parsed = parsed._replace(query=urlencode(query))
    return urlunparse(parsed), connect_args


DATABASE_URL, DB_CONNECT_ARGS = normalize_database_url(DATABASE_URL)

OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
WEB_ADMIN_KEY = os.getenv("WEB_ADMIN_KEY", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "nnnnzubot")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

WELCOME_DEFAULT_BUTTONS = os.getenv(
    "WELCOME_DEFAULT_BUTTONS",
    "新币供需 - https://t.me/gqdh && 新币公群 - https://t.me/gqdh"
)

WELCOME_RANDOM_NAMES = [
    x.strip() for x in os.getenv(
        "WELCOME_RANDOM_NAMES",
        "宝宝,老板,大哥,贵宾,帅哥,美女,小可爱,新朋友,幸运星,尊贵用户,VIP贵宾"
    ).split(",") if x.strip()
]

AI_AGENT_ENABLED = os.getenv("AI_AGENT_ENABLED", "true").lower() == "true"
AI_AGENT_CONFIRM_REQUIRED = os.getenv("AI_AGENT_CONFIRM_REQUIRED", "true").lower() == "true"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# ======================
# APP / BOT
# ======================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_timeout=60,
    connect_args=DB_CONNECT_ARGS,
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()
async def wait_for_db(max_retries: int = 12, delay: int = 5):
    last_error = None

    for i in range(max_retries):
        try:
            print(f"[DB] trying connect ({i+1}/{max_retries})...")
            async with asyncio.timeout(10):
                async with engine.begin() as conn:
                    await conn.execute(text("SELECT 1"))
            print("[DB] connected")
            return True
        except Exception as e:
            last_error = e
            print(f"[DB] connect failed ({i+1}/{max_retries}): {repr(e)}")
            await asyncio.sleep(delay)

    raise last_error
    
redis_client = (
    redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
    )
    if REDIS_URL else None
)

worker_task = None

# RAM fallback nếu Redis không có
user_state = {}
temp = {}
selected_group = {}
selected_lang = {}
private_menu_msg = {}
admin_cache = set()
keyword_cache = []
banned_cache = []

STRANGER_TEXT = (
    '点击此处可以<a href="https://t.me/nnnnzubot?startgroup=true">添加机器人进群</a>\n\n'
    '更多服务，请访问<a href="https://t.me/xbkf/">官方服务</a>'
)

INIT_GROUP_TEXT = "组防骗助手为您服务,我正在进行相关初始化配置请稍后"

LANG_TEXT = {
    "zh": {
        "home": "🏠 首页",
        "admin": "👑 管理员设置",
        "group": "👥 群组管理",
        "lang": "🌐 语言",
        "lang_title": "🌐 语言",
        "lang_vi_ok": "✅ 已选择：越南语",
        "lang_zh_ok": "✅ 已选择：中文",
        "ai_menu": "🤖 OpenAI",
        "ai_prompt": "请输入要让 AI 回复的内容：",
        "ai_wait": "正在思考，请稍候...",
        "ai_missing": "未配置 OpenAI API。",
        "ai_error": "AI 处理失败。",
    },
    "vi": {
        "home": "🏠 Trang chủ",
        "admin": "👑 Cài đặt quản trị",
        "group": "👥 Quản lý nhóm",
        "lang": "🌐 Ngôn ngữ",
        "lang_title": "🌐 Ngôn ngữ",
        "lang_vi_ok": "✅ Đã chọn: Tiếng Việt",
        "lang_zh_ok": "✅ Đã chọn: Tiếng Trung",
        "ai_menu": "🤖 OpenAI",
        "ai_prompt": "Nhập nội dung bạn muốn AI trả lời:",
        "ai_wait": "Đang suy nghĩ, vui lòng chờ...",
        "ai_missing": "Chưa cấu hình OpenAI API.",
        "ai_error": "Xử lý AI thất bại.",
    }
}

AI_AGENT_SYSTEM_PROMPT = """
你是 Telegram 管理机器人里的 AI 管家。
你的任务是帮助 OWNER / 管理员理解如何使用机器人，或者把自然语言请求转换成受限动作。

规则：
1. 只允许输出 JSON。
2. 不允许输出代码块，不允许输出解释性前缀。
3. 如果用户是在询问怎么用，返回 action=help。
4. 如果用户是在要求机器人执行操作，只能从以下动作中选择：
   help
   show_groups
   list_keywords
   rename_group
   lock_group
   unlock_group
   add_keyword
   set_welcome_text
   set_welcome_button
   toggle_welcome
5. 不允许添加管理员、删除管理员、修改 OWNER 权限。
6. 如果用户提到管理员权限，请引导他使用菜单：
   管理员设置 -> 管理员权限
7. 高风险动作（rename_group, lock_group, unlock_group）请设置 need_confirm=true。
8. 如果信息不全，设置 need_more=true，并提出最简短问题。
9. 输出格式固定：
{
  "action": "...",
  "need_confirm": false,
  "need_more": false,
  "question": "",
  "reply": "",
  "params": {}
}
"""

# ======================
# JINJA
# ======================

@pass_context
def _jinja_url_for(context, name: str, **path_params):
    request: Request = context["request"]

    if "filename" in path_params and "path" not in path_params:
        path_params["path"] = path_params.pop("filename")

    url = request.url_for(name, **path_params)

    admin_key = context.get("admin_key", "")
    if admin_key and name not in ("static", "login", "login_post"):
        try:
            url = url.include_query_params(key=admin_key)
        except Exception:
            pass

    return url


templates.env.globals["url_for"] = _jinja_url_for
templates.env.globals["get_flashed_messages"] = lambda *args, **kwargs: []

# ======================
# REDIS HELPERS
# ======================

async def redis_set_json(key: str, value, ex: Optional[int] = None):
    if not redis_client:
        return
    await redis_client.set(key, json.dumps(value, ensure_ascii=False), ex=ex)


async def redis_get_json(key: str, default=None):
    if not redis_client:
        return default
    raw = await redis_client.get(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


async def redis_del(key: str):
    if not redis_client:
        return
    await redis_client.delete(key)


# ======================
# STATE HELPERS
# ======================

async def get_state(uid: int):
    if redis_client:
        return await redis_get_json(f"state:{uid}", None)
    return user_state.get(uid)


async def set_state(uid: int, value: Optional[str]):
    if redis_client:
        if value is None:
            await redis_del(f"state:{uid}")
        else:
            await redis_set_json(f"state:{uid}", value, ex=3600)
    if value is None:
        user_state.pop(uid, None)
    else:
        user_state[uid] = value


async def get_temp(uid: int):
    if redis_client:
        return await redis_get_json(f"temp:{uid}", {})
    return temp.get(uid, {})


async def set_temp(uid: int, value: dict):
    if redis_client:
        await redis_set_json(f"temp:{uid}", value, ex=3600)
    temp[uid] = value


async def get_selected_group(uid: int):
    if redis_client:
        return await redis_get_json(f"sel_group:{uid}", None)
    return selected_group.get(uid)


async def set_selected_group(uid: int, value: Optional[str]):
    if redis_client:
        if value is None:
            await redis_del(f"sel_group:{uid}")
        else:
            await redis_set_json(f"sel_group:{uid}", value, ex=86400)
    if value is None:
        selected_group.pop(uid, None)
    else:
        selected_group[uid] = value


async def get_selected_lang(uid: int):
    if redis_client:
        v = await redis_get_json(f"sel_lang:{uid}", None)
        if v:
            return v
    return selected_lang.get(uid, "zh")


async def set_selected_lang(uid: int, value: str):
    if redis_client:
        await redis_set_json(f"sel_lang:{uid}", value, ex=86400 * 30)
    selected_lang[uid] = value


async def reset(uid):
    user_state.pop(uid, None)
    temp.pop(uid, None)
    if redis_client:
        await redis_del(f"state:{uid}")
        await redis_del(f"temp:{uid}")


# ======================
# BASIC HELPERS
# ======================

def allowed_admin_ids():
    ids = set(admin_cache) | set(ADMIN_IDS)
    if OWNER_ID:
        ids.add(OWNER_ID)
    ids.discard(0)
    return ids


async def get_lang(uid: int) -> str:
    return await get_selected_lang(uid)


async def t(uid: int, key: str) -> str:
    lang = await get_lang(uid)
    return LANG_TEXT.get(lang, LANG_TEXT["zh"]).get(key, key)


def is_allowed_user(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in ADMIN_IDS or user_id in admin_cache


def can_change_language(user_id: int) -> bool:
    return is_allowed_user(user_id)


def check_web_key(key: Optional[str]) -> bool:
    return bool(WEB_ADMIN_KEY) and key == WEB_ADMIN_KEY


def web_authenticated(key: str = "") -> bool:
    return check_web_key(key)


def web_redirect(request: Request, name: str, key: str = ""):
    url = request.url_for(name)
    if key and name not in ("login", "login_post", "static"):
        try:
            url = url.include_query_params(key=key)
        except Exception:
            pass
    return RedirectResponse(url=str(url), status_code=303)


def web_base_context(request: Request, active_page: str = "", admin_key: str = "", **kwargs):
    return {
        "request": request,
        "active_page": active_page,
        "current_year": datetime.now().year,
        "admin_key": admin_key,
        **kwargs
    }


def stranger_start_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="➕ 添加机器人进群",
            url=f"https://t.me/{BOT_USERNAME}?startgroup=true"
        )],
        [InlineKeyboardButton(
            text="🌐 官方服务",
            url="https://t.me/xbkf/"
        )]
    ])


def init_group_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="公群导航", url="https://t.me/gqdh"),
            InlineKeyboardButton(text="供需频道", url="https://t.me/gqdh"),
        ]
    ])


async def ack(c: types.CallbackQuery, text: Optional[str] = None):
    with contextlib.suppress(Exception):
        await c.answer(text=text)


async def allowed_or_ignore(c: types.CallbackQuery):
    if not c.from_user or not is_allowed_user(c.from_user.id):
        await ack(c)
        return False
    return True


# ======================
# BUTTON / TEXT HELPERS
# ======================

def parse_buttons(text_):
    if not text_:
        return None

    rows = []
    for line in text_.split("\n"):
        row = []
        for part in line.split("&&"):
            part = part.strip()
            if not part:
                continue
            if " - " in part:
                t_, u = part.split(" - ", 1)
            elif "-" in part:
                t_, u = part.split("-", 1)
            else:
                continue
            row.append({"text": t_.strip(), "url": u.strip()})
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


async def send_preview(chat_id, text_=None, image=None, button=None, parse_mode=None):
    kb = build_buttons(parse_buttons(button))
    try:
        if image:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=image,
                caption=text_ or "",
                reply_markup=kb,
                parse_mode=parse_mode
            )
        return await bot.send_message(
            chat_id=chat_id,
            text=text_ or " ",
            reply_markup=kb,
            parse_mode=parse_mode
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


def extract_first_int(text_: str):
    if not text_:
        return None
    buf = ""
    for ch in text_:
        if ch.isdigit():
            buf += ch
        elif buf:
            break
    return int(buf) if buf else None


def default_welcome_button_text():
    return WELCOME_DEFAULT_BUTTONS


async def is_chat_admin(bot_: Bot, chat_id: Union[int, str], user_id: int) -> bool:
    try:
        member = await bot_.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def lock_group(chat_id: Union[int, str]):
    await bot.set_chat_permissions(
        chat_id=chat_id,
        permissions=ChatPermissions(
            can_send_messages=False,
            can_send_audios=False,
            can_send_documents=False,
            can_send_photos=False,
            can_send_videos=False,
            can_send_video_notes=False,
            can_send_voice_notes=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
        )
    )


async def unlock_group(chat_id: Union[int, str]):
    await bot.set_chat_permissions(
        chat_id=chat_id,
        permissions=ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
        )
    )


def normalize_form_text(text_: str) -> str:
    return (text_ or "").replace("：", ":").strip()


def parse_guarantee_form(text_: str) -> dict:
    data = {}
    for raw in normalize_form_text(text_).splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("担保表单"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        data[k.strip()] = v.strip()
    return data


def build_group_title_from_form(data: dict) -> str:
    group_name = (data.get("组别") or "").strip()
    number = (data.get("编号") or "").strip()
    rule = (data.get("规则") or "").strip().replace(" ", "")
    name = (data.get("名字") or "").strip()
    title = f"{group_name} {number}-{rule} {name}".strip()
    return title[:128]


def build_welcome_text(template: str, user: types.User, chat_title: str = "", chat_id: str = "") -> str:
    first_name = escape(user.first_name or "")
    last_name = escape(user.last_name or "")
    full_name = escape(" ".join(
        x for x in [user.first_name or "", user.last_name or ""] if x
    ).strip())
    username = f"@{user.username}" if user.username else "-"
    username = escape(username)
    rand_name = random.choice(WELCOME_RANDOM_NAMES) if WELCOME_RANDOM_NAMES else "VIP用户"
    mention_label = first_name or full_name or "新成员"
    mention = f'<a href="tg://user?id={user.id}">{mention_label}</a>'
    chat_title = escape(chat_title or "")
    chat_id = escape(str(chat_id or ""))

    text_ = template or ""

    replacements = {
        "{first_name}": first_name,
        "{last_name}": last_name,
        "{full_name}": full_name,
        "{username}": username,
        "{mention}": mention,
        "{rand_name}": rand_name,
        "{chat_title}": chat_title,
        "{chat_id}": chat_id,

        "(*name*)": mention,
        "(*fullname*)": full_name,
        "(*username*)": username,
        "(*rand*)": rand_name,
        "(*group*)": chat_title,
        "(*groupid*)": chat_id,
    }

    for old, new in replacements.items():
        text_ = text_.replace(old, new)

    return text_


def parse_local_ai_agent_request(text_: str):
    raw = (text_ or "").strip()
    lower = raw.lower()

    if any(x in lower for x in [
        "hướng dẫn", "cách dùng", "cách sử dụng", "help", "how to use",
        "怎么用", "如何使用", "如何添加关键词"
    ]):
        return {
            "action": "help",
            "need_confirm": False,
            "need_more": False,
            "question": "",
            "reply": (
                "📘 Hướng dẫn nhanh:\n\n"
                "1. Thêm từ khóa:\n"
                "- Vào: 管理员设置 -> 关键词 -> 添加\n"
                "- Hoặc nói: 帮我添加关键词 上課\n\n"
                "2. Đổi tên nhóm:\n"
                "- Chọn nhóm trước\n"
                "- Nói: đổi tên nhóm thành E88\n\n"
                "3. Khóa / mở nhóm:\n"
                "- Nói: 锁群 / 开群\n\n"
                "4. Quản lý welcome:\n"
                "- Vào: 群组欢迎\n"
                "- Dùng biến: (*name*), (*group*), (*groupid*), (*rand*)\n\n"
                "5. Cấp quyền admin:\n"
                "- Vào: 管理员设置 -> 管理员权限\n"
                "- Chỉ OWNER mới dùng được\n\n"
                "6. Thoát AI 管家:\n"
                "- /cancel"
            ),
            "params": {}
        }

    if raw in ["开群", "mở nhóm", "mo nhom", "unlock group", "unlock"]:
        return {
            "action": "unlock_group",
            "need_confirm": True,
            "need_more": False,
            "question": "",
            "reply": "",
            "params": {}
        }

    if raw in ["锁群", "khoá nhóm", "khóa nhóm", "khoa nhom", "lock group", "lock"]:
        return {
            "action": "lock_group",
            "need_confirm": True,
            "need_more": False,
            "question": "",
            "reply": "",
            "params": {}
        }

    if any(x in lower for x in ["đổi tên nhóm", "doi ten nhom", "改群名", "修改群名"]):
        title = ""
        for sep in ["thành", "为", "成", "to"]:
            if sep in lower:
                idx = lower.find(sep)
                title = raw[idx + len(sep):].strip()
                break

        if not title:
            return {
                "action": "rename_group",
                "need_confirm": False,
                "need_more": True,
                "question": "请提供新名称。",
                "reply": "",
                "params": {}
            }

        return {
            "action": "rename_group",
            "need_confirm": True,
            "need_more": False,
            "question": "",
            "reply": "",
            "params": {"title": title}
        }

    if any(x in raw for x in ["帮我添加关键词", "添加关键词", "thêm từ khoá", "thêm từ khóa", "add keyword"]):
        key = raw
        for prefix in ["帮我添加关键词", "添加关键词", "thêm từ khoá", "thêm từ khóa", "add keyword"]:
            if prefix.lower() in lower:
                pos = lower.find(prefix.lower())
                key = raw[pos + len(prefix):].strip()
                break

        if not key:
            return {
                "action": "add_keyword",
                "need_confirm": False,
                "need_more": True,
                "question": "缺少关键词，请发送关键词内容。",
                "reply": "",
                "params": {}
            }

        return {
            "action": "add_keyword",
            "need_confirm": False,
            "need_more": False,
            "question": "",
            "reply": "",
            "params": {"key": key}
        }

    if any(x in lower for x in ["cấp quyền admin", "thêm admin", "添加管理员", "add admin"]):
        return {
            "action": "help",
            "need_confirm": False,
            "need_more": False,
            "question": "",
            "reply": "请使用菜单：管理员设置 -> 管理员权限。只有 OWNER 可以添加或删除管理员。",
            "params": {}
        }

    if any(x in lower for x in ["set welcome", "设置欢迎词", "đặt welcome", "sửa welcome", "welcome text"]):
        return {
            "action": "set_welcome_text",
            "need_confirm": False,
            "need_more": True,
            "question": "请发送欢迎文本内容。",
            "reply": "",
            "params": {}
        }

    return None


def strip_code_fences(text_: str) -> str:
    s = (text_ or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    return s.strip()


async def ai_agent_parse(user_text: str):
    local_result = parse_local_ai_agent_request(user_text)
    if local_result:
        return local_result

    if not openai_client:
        return {
            "action": "help",
            "need_confirm": False,
            "need_more": False,
            "question": "",
            "reply": "未配置 OpenAI API。",
            "params": {}
        }

    try:
        resp = await openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": AI_AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": user_text}
            ],
            temperature=0.2
        )
        content = strip_code_fences(resp.choices[0].message.content or "")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("invalid json")

        return {
            "action": data.get("action", "help"),
            "need_confirm": bool(data.get("need_confirm", False)),
            "need_more": bool(data.get("need_more", False)),
            "question": data.get("question", ""),
            "reply": data.get("reply", ""),
            "params": data.get("params", {}) or {}
        }
    except Exception as e:
        print("[AI AGENT PARSE ERROR]", e)
        return {
            "action": "help",
            "need_confirm": False,
            "need_more": False,
            "question": "",
            "reply": "我没有完全理解你的要求，请换一种更明确的说法。",
            "params": {}
        }


# ======================
# MODELS
# ======================

class Keyword(Base):
    __tablename__ = "keywords"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, index=True)
    mode = Column(String, default="exact")
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
    delete_after = Column(Integer, default=0)
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


class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True, index=True)
    note = Column(String, default="")
    created_at = Column(Integer, default=0)


class BannedWord(Base):
    __tablename__ = "banned_words"
    id = Column(Integer, primary_key=True)
    word = Column(String, unique=True, index=True)


# ======================
# MENUS
# ======================

def start_menu_kb(uid: Optional[int] = None):
    kb = [
        [InlineKeyboardButton(text="👑 管理员设置", callback_data="admin_menu")],
        [InlineKeyboardButton(text="👥 群组管理", callback_data="group_menu")],
    ]
    if uid is not None and can_change_language(uid):
        kb.append([InlineKeyboardButton(text="🌐 语言", callback_data="lang_menu")])
        kb.append([InlineKeyboardButton(text="🤖 OpenAI", callback_data="ai_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 管理员权限", callback_data="admin_user_menu")],
        [InlineKeyboardButton(text="📌 关键词", callback_data="kw_menu")],
        [InlineKeyboardButton(text="👋 群组欢迎", callback_data="wl_menu")],
        [InlineKeyboardButton(text="📅 定时发送", callback_data="auto_menu")],
        [InlineKeyboardButton(text="🚫 禁词", callback_data="ban_menu")],
        [InlineKeyboardButton(text="🧠 AI管家", callback_data="ai_agent_menu")],
        [InlineKeyboardButton(text="🌐 语言", callback_data="lang_menu")],
        [InlineKeyboardButton(text="🤖 OpenAI", callback_data="ai_menu")],
        [InlineKeyboardButton(text="⬅️ 返回", callback_data="back_start")],
    ])


def admin_user_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ 添加管理员", callback_data="admin_user_add")],
        [InlineKeyboardButton(text="📋 管理员列表", callback_data="admin_user_list")],
        [InlineKeyboardButton(text="⬅️ 返回", callback_data="admin_menu")],
    ])


def group_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 群组列表", callback_data="group_list")],
        [InlineKeyboardButton(text="➕ 选择群组", callback_data="group_pick")],
        [InlineKeyboardButton(text="📝 修改群名", callback_data="group_rename")],
        [
            InlineKeyboardButton(text="🔒 锁群", callback_data="group_lock"),
            InlineKeyboardButton(text="🔓 开群", callback_data="group_unlock")
        ],
        [InlineKeyboardButton(text="⬅️ 返回", callback_data="back_start")],
    ])


def lang_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇻🇳 越南语", callback_data="lang_vi")],
        [InlineKeyboardButton(text="🇨🇳 中文", callback_data="lang_zh")],
        [InlineKeyboardButton(text="⬅️ 返回", callback_data="back_start")],
    ])


def group_select_kb(groups):
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
    kb.append([InlineKeyboardButton(text="⬅️ 返回", callback_data="back_start")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def kw_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ 添加", callback_data="kw_add")],
        [InlineKeyboardButton(text="📋 列表", callback_data="kw_list")],
        [InlineKeyboardButton(text="🔙 首页", callback_data="back_start")],
    ])


def wl_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ 新建", callback_data="wl_add")],
        [InlineKeyboardButton(text="📋 列表", callback_data="wl_list")],
        [InlineKeyboardButton(text="🔙 首页", callback_data="back_start")],
    ])


def auto_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ 新建", callback_data="auto_add")],
        [InlineKeyboardButton(text="📋 列表", callback_data="auto_list")],
        [InlineKeyboardButton(text="🔙 首页", callback_data="back_start")],
    ])


def ban_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ 添加", callback_data="ban_add")],
        [InlineKeyboardButton(text="📋 列表", callback_data="ban_list")],
        [InlineKeyboardButton(text="⬅️ 返回", callback_data="admin_menu")],
    ])


# ======================
# DB / VIEW HELPERS
# ======================

async def show_admin_user_list(message):
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(AdminUser).order_by(AdminUser.id.desc())
        )).scalars().all()

    kb = []

    if OWNER_ID:
        kb.append([
            InlineKeyboardButton(
                text=f"👑 OWNER: {OWNER_ID}",
                callback_data="admin_user_noop"
            )
        ])

    for i, row in enumerate(rows, start=1):
        kb.append([
            InlineKeyboardButton(
                text=f"{i}. {row.user_id}",
                callback_data="admin_user_noop"
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=f"admin_user_del_{row.user_id}"
            )
        ])

    kb.append([InlineKeyboardButton(text="⬅️ 返回", callback_data="admin_user_menu")])

    await safe_edit(
        message,
        "👤 管理员列表" if rows or OWNER_ID else "暂无管理员。",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


async def show_kw_list(message):
    async with SessionLocal() as db:
        rows = (await db.execute(select(Keyword).order_by(Keyword.id.desc()))).scalars().all()

    kb = []
    for i, k in enumerate(rows, start=1):
        kb.append([
            InlineKeyboardButton(
                text=f"{i}. {k.key} ({'✅' if k.active else '❌'})",
                callback_data=f"kw_view_{k.id}"
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"kw_del_{k.id}")
        ])
    kb.append([InlineKeyboardButton(text="🔙", callback_data="kw_menu")])

    await safe_edit(
        message,
        "关键词列表" if rows else "暂无关键词。",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


async def show_kw_view(message, kid):
    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)
    if not k:
        return await message.answer("关键词不存在。")

    await safe_edit(
        message,
        f"关键词详情\n\n"
        f"关键词：{k.key}\n"
        f"模式：{'精确匹配' if k.mode == 'exact' else '包含匹配'}\n"
        f"启用：{'✅' if k.active else '❌'}\n"
        f"按钮：{'✅' if k.button else '❌'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"模式：{'✅ 精确匹配' if k.mode == 'exact' else '包含匹配'}", callback_data=f"kw_mode_{k.id}")],
            [InlineKeyboardButton(text=f"状态：{'✅ 开启' if k.active else '❌ 关闭'}", callback_data=f"kw_toggle_{k.id}")],
            [InlineKeyboardButton(text="📝 修改关键词", callback_data=f"kw_key_{k.id}")],
            [InlineKeyboardButton(text="📝 修改文本", callback_data=f"kw_text_{k.id}")],
            [InlineKeyboardButton(text="🖼 修改媒体", callback_data=f"kw_img_{k.id}")],
            [InlineKeyboardButton(text="🔤 修改按钮", callback_data=f"kw_btn_{k.id}")],
            [InlineKeyboardButton(text="👀 预览", callback_data=f"kw_pre_{k.id}")],
            [InlineKeyboardButton(text="⬅️ 返回", callback_data="kw_list")],
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
        "群组欢迎设置" if rows else "暂无群组欢迎配置。",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


async def show_wl_view(message, wid):
    async with SessionLocal() as db:
        w = await db.get(WelcomeSetting, wid)
    if not w:
        return await message.answer("欢迎配置不存在。")

    await safe_edit(
        message,
        f"群组欢迎详情\n\n"
        f"状态：{'开启' if w.active else '关闭'}\n"
        f"删除消息：{w.delete_after if w.delete_after else '不删除'} 分钟\n"
        f"图片：{'✅' if w.image else '❌'}\n"
        f"按钮：{'✅' if w.button else '❌'}\n"
        f"文本：{'✅' if w.text else '❌'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"状态：{'✅ 开启' if w.active else '❌ 关闭'}", callback_data=f"wl_toggle_{w.id}")],
            [InlineKeyboardButton(text=f"删除消息：{w.delete_after if w.delete_after else 0} 分钟", callback_data=f"wl_delmin_{w.id}")],
            [InlineKeyboardButton(text="📌 置顶", callback_data=f"wl_pin_{w.id}")],
            [InlineKeyboardButton(text="📝 修改文本", callback_data=f"wl_text_{w.id}")],
            [InlineKeyboardButton(text="📷 修改媒体", callback_data=f"wl_img_{w.id}")],
            [InlineKeyboardButton(text="🔤 修改按钮", callback_data=f"wl_btn_{w.id}")],
            [InlineKeyboardButton(text="👀 预览", callback_data=f"wl_pre_{w.id}")],
            [InlineKeyboardButton(text="⬅️ 返回", callback_data="wl_list")],
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
        "定时发送列表" if rows else "暂无定时发送。",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


async def show_auto_view(message, pid):
    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)
    if not p:
        return await message.answer("定时发送不存在。")

    last_time = "-"
    if p.last_sent_ts:
        last_time = datetime.fromtimestamp(p.last_sent_ts).strftime("%Y-%m-%d %H:%M:%S")

    await safe_edit(
        message,
        f"定时发送详情\n\n"
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
            [InlineKeyboardButton(text="👀 预览", callback_data=f"auto_pre_{p.id}")],
            [InlineKeyboardButton(text="⏩ 间隔时间", callback_data=f"auto_int_{p.id}")],
            [InlineKeyboardButton(text="📅 开始时间", callback_data=f"auto_start_{p.id}")],
            [InlineKeyboardButton(text="📅 结束时间", callback_data=f"auto_end_{p.id}")],
            [InlineKeyboardButton(text="⬅️ 返回", callback_data="auto_list")],
        ])
    )


async def show_banned_list(message):
    async with SessionLocal() as db:
        rows = (await db.execute(select(BannedWord).order_by(BannedWord.id.asc()))).scalars().all()

    kb = []
    for i, row in enumerate(rows, start=1):
        kb.append([
            InlineKeyboardButton(text=f"{i}. {row.word}", callback_data="ban_noop"),
            InlineKeyboardButton(text="🗑", callback_data=f"ban_del_{row.id}")
        ])
    kb.append([InlineKeyboardButton(text="⬅️ 返回", callback_data="ban_menu")])

    await safe_edit(
        message,
        "🚫 禁词列表" if rows else "暂无禁词。",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


async def execute_ai_action(m: types.Message, uid: int, action: str, params: dict):
    chat_id = await get_selected_group(uid)

    if action == "help":
        help_text = (
            "📘 Hướng dẫn nhanh:\n\n"
            "1. Thêm từ khóa:\n"
            "- 说：帮我添加关键词 上課\n"
            "- Hoặc vào: 管理员设置 -> 关键词 -> 添加\n\n"
            "2. Đổi tên nhóm:\n"
            "- 说：đổi tên nhóm thành E88\n"
            "- Hoặc: 改群名为 E88\n\n"
            "3. Khóa / mở nhóm:\n"
            "- 说：锁群\n"
            "- 说：开群\n\n"
            "4. Welcome text:\n"
            "- Dùng biến: (*name*), (*group*), (*groupid*), (*rand*)\n\n"
            "5. Cấp quyền admin:\n"
            "- Dùng nút: 管理员设置 -> 管理员权限\n"
            "- Chỉ OWNER mới dùng được\n\n"
            "6. Thoát AI 管家:\n"
            "- /cancel"
        )
        return await m.answer(params.get("text") or help_text)

    if action == "show_groups":
        groups = await get_all_groups()
        if not groups:
            return await m.answer("暂无群组。")
        text_out = "群组列表：\n\n" + "\n".join(
            f"{i}. {g.title or g.chat_id} ({g.chat_id})"
            for i, g in enumerate(groups, start=1)
        )
        return await m.answer(text_out)

    if action == "list_keywords":
        return await show_kw_list(m)

    if action == "rename_group":
        if not chat_id:
            return await m.answer("请先选择群组。")
        new_title = (params.get("title") or "").strip()
        if not new_title:
            return await m.answer("缺少新的群名。")
        await bot.set_chat_title(chat_id=chat_id, title=new_title[:128])
        async with SessionLocal() as db:
            row = (await db.execute(select(BotGroup).where(BotGroup.chat_id == str(chat_id)))).scalars().first()
            if row:
                row.title = new_title[:128]
                row.updated_at = int(time.time())
                await db.commit()
        return await m.answer(f"✅ 群名已修改为：{new_title}")

    if action == "lock_group":
        if not chat_id:
            return await m.answer("请先选择群组。")
        await lock_group(chat_id)
        return await m.answer("🔒 已锁群。")

    if action == "unlock_group":
        if not chat_id:
            return await m.answer("请先选择群组。")
        await unlock_group(chat_id)
        return await m.answer("🔓 已开群。")

    if action == "add_keyword":
        key = (params.get("key") or "").strip()
        if not key:
            return await m.answer("缺少关键词。")

        async with SessionLocal() as db:
            exists = (await db.execute(select(Keyword).where(Keyword.key == key))).scalars().first()
            if exists:
                return await m.answer("关键词已存在。")
            row = Keyword(key=key, mode="exact", active=1, text="", image="", button="")
            db.add(row)
            await db.commit()
            await db.refresh(row)

        await reload_keyword_cache()
        return await m.answer(f"✅ 已添加关键词：{key}")

    if action == "set_welcome_text":
        if not chat_id:
            return await m.answer("请先选择群组。")
        text_value = (params.get("text") or "").strip()
        if not text_value:
            return await m.answer("缺少欢迎文本。")

        async with SessionLocal() as db:
            row = (await db.execute(
                select(WelcomeSetting).where(WelcomeSetting.chat_id == str(chat_id))
            )).scalars().first()
            if not row:
                row = WelcomeSetting(chat_id=str(chat_id), active=1)
                db.add(row)
            row.text = text_value
            await db.commit()

        return await m.answer("✅ 欢迎文本已更新。")

    if action == "set_welcome_button":
        if not chat_id:
            return await m.answer("请先选择群组。")
        button_value = (params.get("button") or "").strip()
        if not button_value:
            return await m.answer("缺少按钮内容。")

        async with SessionLocal() as db:
            row = (await db.execute(
                select(WelcomeSetting).where(WelcomeSetting.chat_id == str(chat_id))
            )).scalars().first()
            if not row:
                row = WelcomeSetting(chat_id=str(chat_id), active=1)
                db.add(row)
            row.button = button_value
            await db.commit()

        return await m.answer("✅ 欢迎按钮已更新。")

    if action == "toggle_welcome":
        if not chat_id:
            return await m.answer("请先选择群组。")

        enabled = params.get("enabled")
        async with SessionLocal() as db:
            row = (await db.execute(
                select(WelcomeSetting).where(WelcomeSetting.chat_id == str(chat_id))
            )).scalars().first()
            if not row:
                row = WelcomeSetting(chat_id=str(chat_id), active=1)
                db.add(row)
            row.active = 1 if enabled in (True, 1, "1", "true", "on", "开启") else 0
            await db.commit()

        return await m.answer(f"✅ 欢迎已{'开启' if row.active else '关闭'}。")

    return await m.answer("这个操作暂不支持。")


# ======================
# TRACK BOT IN GROUP
# ======================

@dp.my_chat_member()
async def track_bot_membership(event: types.ChatMemberUpdated):
    chat = event.chat
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    print(f"[MY_CHAT_MEMBER] chat_id={chat.id} type={chat.type} old={old_status} new={new_status}")

    if chat.type not in ("group", "supergroup"):
        return

    chat_id = str(chat.id)
    now_ts = int(time.time())

    bot_just_added = old_status in ("left", "kicked") and new_status in ("member", "administrator")

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

        if bot_just_added:
            with contextlib.suppress(Exception):
                await bot.send_message(
                    chat_id=chat.id,
                    text=INIT_GROUP_TEXT,
                    reply_markup=init_group_kb()
                )

    if new_status in ("left", "kicked"):
        async with SessionLocal() as db:
            await db.execute(delete(BotGroup).where(BotGroup.chat_id == chat_id))
            await db.commit()


# ======================
# START / HOME
# ======================

@dp.message(CommandStart())
async def start(m: types.Message):
    if not m.from_user:
        return

    uid = m.from_user.id
    print("[START HANDLER]", uid, m.chat.type, m.text)

    if m.chat.type == "private" and not is_allowed_user(uid):
        await m.answer(
            STRANGER_TEXT,
            parse_mode="HTML",
            disable_web_page_preview=False
        )
        return

    await reset(uid)

    if m.chat.type == "private":
        old_msg_id = private_menu_msg.get(uid)
        edited = False

        if old_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=m.chat.id,
                    message_id=old_msg_id,
                    text=await t(uid, "home"),
                    reply_markup=start_menu_kb(uid)
                )
                edited = True
            except Exception as e:
                print("[START EDIT OLD MENU ERROR]", e)

        if edited:
            return

        msg = await m.answer(await t(uid, "home"), reply_markup=start_menu_kb(uid))
        private_menu_msg[uid] = msg.message_id
        return

    groups = await get_admin_groups()
    if groups and is_allowed_user(uid):
        await m.answer("👥 请选择要管理的群组：", reply_markup=group_select_kb(groups))
        return

    if is_allowed_user(uid):
        await m.answer(await t(uid, "home"), reply_markup=start_menu_kb(uid))


@dp.message(Command("ping"))
async def ping_cmd(m: types.Message):
    print("[PING RECEIVED]", m.from_user.id if m.from_user else None, m.chat.id)
    await m.answer("pong")


@dp.message(F.text == "/cancel")
async def cancel(m: types.Message):
    if not m.from_user:
        return
    await reset(m.from_user.id)
    if is_allowed_user(m.from_user.id):
        await m.answer("已取消操作。", reply_markup=start_menu_kb(m.from_user.id))


@dp.message(F.text == "/ai")
async def ai_cmd(m: types.Message):
    if not m.from_user or not is_allowed_user(m.from_user.id):
        return
    uid = m.from_user.id
    await reset(uid)
    await set_state(uid, "ai_prompt")
    await set_temp(uid, {})
    await m.answer(await t(uid, "ai_prompt"))


@dp.message(Command("addadmin"))
async def add_admin_cmd(m: types.Message):
    if not m.from_user or m.from_user.id != OWNER_ID:
        return await m.answer("❌ 只有 OWNER 可以添加管理员。")

    parts = (m.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await m.answer("用法：/addadmin USER_ID")

    user_id = int(parts[1])
    if user_id == OWNER_ID:
        return await m.answer("不能添加 OWNER。")

    async with SessionLocal() as db:
        row = (await db.execute(select(AdminUser).where(AdminUser.user_id == user_id))).scalars().first()
        if not row:
            db.add(AdminUser(user_id=user_id, note="", created_at=int(time.time())))
            await db.commit()

    await load_admin_cache()
    await m.answer(f"✅ 已添加管理员：{user_id}")


@dp.message(Command("deladmin"))
async def del_admin_cmd(m: types.Message):
    if not m.from_user or m.from_user.id != OWNER_ID:
        return await m.answer("❌ 只有 OWNER 可以删除管理员。")

    parts = (m.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await m.answer("用法：/deladmin USER_ID")

    user_id = int(parts[1])
    if user_id == OWNER_ID:
        return await m.answer("不能删除 OWNER。")

    async with SessionLocal() as db:
        await db.execute(delete(AdminUser).where(AdminUser.user_id == user_id))
        await db.commit()

    await load_admin_cache()
    await m.answer(f"✅ 已删除管理员：{user_id}")


@dp.message(Command("admins"))
async def list_admins_cmd(m: types.Message):
    if not m.from_user or m.from_user.id != OWNER_ID:
        return await m.answer("❌ 只有 OWNER 可以查看管理员。")

    ids = sorted(allowed_admin_ids())
    txt = "👑 管理员列表：\n\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(ids, start=1))
    await m.answer(txt)


@dp.callback_query(F.data == "back_start")
async def back_start(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await reset(c.from_user.id)
    await safe_edit(c.message, await t(c.from_user.id, "home"), reply_markup=start_menu_kb(c.from_user.id))


# ======================
# MAIN MENU
# ======================

@dp.callback_query(F.data == "admin_menu")
async def admin_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await safe_edit(c.message, "👑 管理员设置", reply_markup=admin_menu_kb())


@dp.callback_query(F.data == "admin_user_menu")
async def admin_user_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    if c.from_user.id != OWNER_ID:
        return await c.message.answer("❌ 只有 OWNER 可以管理管理员。")
    await safe_edit(c.message, "👤 管理员权限", reply_markup=admin_user_menu_kb())


@dp.callback_query(F.data == "admin_user_add")
async def admin_user_add(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    if c.from_user.id != OWNER_ID:
        return await c.message.answer("❌ 只有 OWNER 可以添加管理员。")

    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "admin_add_id")
    await set_temp(uid, {})
    await c.message.answer("请输入要添加的管理员 user_id：")


@dp.callback_query(F.data == "admin_user_list")
async def admin_user_list(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    if c.from_user.id != OWNER_ID:
        return await c.message.answer("❌ 只有 OWNER 可以查看管理员。")
    await show_admin_user_list(c.message)


@dp.callback_query(F.data.startswith("admin_user_del_"))
async def admin_user_del(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    if c.from_user.id != OWNER_ID:
        return await c.message.answer("❌ 只有 OWNER 可以删除管理员。")

    user_id = int(c.data.split("_")[-1])
    if user_id == OWNER_ID:
        return await c.message.answer("不能删除 OWNER。")

    async with SessionLocal() as db:
        await db.execute(delete(AdminUser).where(AdminUser.user_id == user_id))
        await db.commit()

    await load_admin_cache()
    await show_admin_user_list(c.message)


@dp.callback_query(F.data == "admin_user_noop")
async def admin_user_noop(c: types.CallbackQuery):
    await ack(c)


@dp.callback_query(F.data == "group_menu")
async def group_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await safe_edit(c.message, "👥 群组管理", reply_markup=group_menu_kb())


@dp.callback_query(F.data == "lang_menu")
async def lang_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await safe_edit(c.message, await t(c.from_user.id, "lang_title"), reply_markup=lang_menu_kb())


@dp.callback_query(F.data == "ai_menu")
async def ai_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "ai_prompt")
    await set_temp(uid, {})
    await c.message.answer(await t(uid, "ai_prompt"))


@dp.callback_query(F.data == "ai_agent_menu")
async def ai_agent_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    if not AI_AGENT_ENABLED:
        return await c.message.answer("AI 管家未开启。")

    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "ai_agent")
    await set_temp(uid, {})

    await c.message.answer(
        "🧠 AI管家已开启。\n"
        "我可以帮助你了解如何使用机器人，或执行部分管理操作。\n\n"
        "例如：\n"
        "- 帮我添加关键词 上課\n"
        "- 把当前群改名为 E88\n"
        "- 设置欢迎词为 ...\n"
        "- 锁群\n"
        "- 开群\n"
        "- 如何使用欢迎功能？\n\n"
        "⚠️ 管理员权限请使用按钮菜单：管理员设置 -> 管理员权限\n"
        "输入 /cancel 退出。"
    )


@dp.callback_query(F.data == "group_list")
async def group_list(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    groups = await get_all_groups()

    kb = []
    for g in groups:
        kb.append([
            InlineKeyboardButton(
                text=f"{g.title or g.chat_id} ({'管理员' if g.is_admin else '成员'})",
                callback_data=f"pick_group_{g.chat_id}"
            )
        ])
    kb.append([InlineKeyboardButton(text="⬅️ 返回", callback_data="group_menu")])

    await safe_edit(
        c.message,
        "📋 群组列表" if groups else "暂无已保存的群组。",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )


@dp.callback_query(F.data == "group_pick")
async def group_pick(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    groups = await get_all_groups()
    if not groups:
        return await c.message.answer("暂无群组。")
    await safe_edit(c.message, "➕ 请选择群组：", reply_markup=group_select_kb(groups))


@dp.callback_query(F.data.startswith("pick_group_"))
async def pick_group(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    chat_id = c.data.replace("pick_group_", "")
    uid = c.from_user.id

    await set_selected_group(uid, chat_id)

    async with SessionLocal() as db:
        g = (await db.execute(
            select(BotGroup).where(BotGroup.chat_id == chat_id)
        )).scalars().first()

    title = g.title if g and g.title else chat_id

    await safe_edit(
        c.message,
        f"✅ 已选择群组：\n{title}\n\n{await t(uid, 'home')}",
        reply_markup=start_menu_kb(uid)
    )


@dp.callback_query(F.data == "group_rename")
async def group_rename(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    chat_id = await get_selected_group(uid)
    if not chat_id:
        return await c.message.answer("请先选择群组。")

    await reset(uid)
    await set_state(uid, "group_rename")
    await set_temp(uid, {"chat_id": chat_id})
    await c.message.answer("请输入新的群名：")


@dp.callback_query(F.data == "group_lock")
async def group_lock_cb(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    chat_id = await get_selected_group(uid)
    if not chat_id:
        return await c.message.answer("请先选择群组。")
    try:
        await lock_group(chat_id)
        await c.message.answer("🔒 已锁群。")
    except Exception as e:
        await c.message.answer(f"❌ 锁群失败：{e}")


@dp.callback_query(F.data == "group_unlock")
async def group_unlock_cb(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    chat_id = await get_selected_group(uid)
    if not chat_id:
        return await c.message.answer("请先选择群组。")
    try:
        await unlock_group(chat_id)
        await c.message.answer("🔓 已开群。")
    except Exception as e:
        await c.message.answer(f"❌ 开群失败：{e}")


@dp.callback_query(F.data == "lang_vi")
async def lang_vi(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await set_selected_lang(c.from_user.id, "vi")
    await safe_edit(c.message, await t(c.from_user.id, "lang_vi_ok"), reply_markup=start_menu_kb(c.from_user.id))


@dp.callback_query(F.data == "lang_zh")
async def lang_zh(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await set_selected_lang(c.from_user.id, "zh")
    await safe_edit(c.message, await t(c.from_user.id, "lang_zh_ok"), reply_markup=start_menu_kb(c.from_user.id))


# ======================
# KEYWORD MENU
# ======================

@dp.callback_query(F.data == "kw_menu")
async def kw_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await safe_edit(c.message, "📌 关键词", reply_markup=kw_menu_kb())


@dp.callback_query(F.data == "kw_add")
async def kw_add(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "kw_add_key")
    await set_temp(uid, {})
    await c.message.answer(
        "请输入关键词。\n"
        "如果要一次添加多个关键词，请每行一个。"
    )


@dp.callback_query(F.data == "kw_list")
async def kw_list_cb(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await show_kw_list(c.message)


@dp.callback_query(F.data.startswith("kw_view_"))
async def kw_view(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await show_kw_view(c.message, int(c.data.split("_")[-1]))


@dp.callback_query(F.data.startswith("kw_toggle_"))
async def kw_toggle(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    kid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)
        if not k:
            return await c.message.answer("未找到关键词。")
        k.active = 0 if k.active else 1
        await db.commit()
    await reload_keyword_cache()
    await show_kw_view(c.message, kid)


@dp.callback_query(F.data.startswith("kw_mode_"))
async def kw_mode(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    kid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)
        if not k:
            return await c.message.answer("未找到关键词。")
        k.mode = "contains" if k.mode == "exact" else "exact"
        await db.commit()
    await reload_keyword_cache()
    await show_kw_view(c.message, kid)


@dp.callback_query(F.data.startswith("kw_key_"))
async def kw_key(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    kid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "kw_edit_key")
    await set_temp(uid, {"id": kid})
    await c.message.answer("请输入新的关键词：")


@dp.callback_query(F.data.startswith("kw_text_"))
async def kw_text(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    kid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "kw_edit_text")
    await set_temp(uid, {"id": kid})
    await c.message.answer("请输入回复文本：")


@dp.callback_query(F.data.startswith("kw_img_"))
async def kw_img(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    kid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "kw_edit_image")
    await set_temp(uid, {"id": kid})
    await c.message.answer("请发送图片，或输入图片 URL / file_id：")


@dp.callback_query(F.data.startswith("kw_btn_"))
async def kw_btn(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    kid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "kw_edit_button")
    await set_temp(uid, {"id": kid})
    await c.message.answer(
        "按钮格式：\n"
        "Google - https://google.com && YouTube - https://youtube.com\n"
        "每一行代表一排按钮。"
    )


@dp.callback_query(F.data.startswith("kw_pre_"))
async def kw_pre(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    kid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        k = await db.get(Keyword, kid)
    if not k:
        return await c.message.answer("关键词不存在。")
    await send_preview(chat_id=c.from_user.id, text_=k.text, image=k.image, button=k.button)


@dp.callback_query(F.data.startswith("kw_del_"))
async def kw_del(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await ack(c, "已删除")
    kid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        await db.execute(delete(Keyword).where(Keyword.id == kid))
        await db.commit()
    await reload_keyword_cache()
    await show_kw_list(c.message)


# ======================
# WELCOME MENU
# ======================

@dp.callback_query(F.data == "wl_menu")
async def wl_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await safe_edit(c.message, "👋 群组欢迎", reply_markup=wl_menu_kb())


@dp.callback_query(F.data == "wl_add")
async def wl_add(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "wl_add_chat")
    await set_temp(uid, {})
    await c.message.answer("请输入群组 chat_id：")


@dp.callback_query(F.data == "wl_list")
async def wl_list(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await show_wl_list(c.message)


@dp.callback_query(F.data.startswith("wl_view_"))
async def wl_view(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await show_wl_view(c.message, int(c.data.split("_")[-1]))


@dp.callback_query(F.data.startswith("wl_toggle_"))
async def wl_toggle(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    wid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        w = await db.get(WelcomeSetting, wid)
        if not w:
            return await c.message.answer("未找到。")
        w.active = 0 if w.active else 1
        await db.commit()
    await show_wl_view(c.message, wid)


@dp.callback_query(F.data.startswith("wl_text_"))
async def wl_text(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    wid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "wl_edit_text")
    await set_temp(uid, {"id": wid})
    await c.message.answer(
        "请输入欢迎文本：\n\n"
        "支持变量：\n"
        "(*name*) / {mention}        - 提及新成员\n"
        "(*fullname*) / {full_name}  - 完整名字\n"
        "(*username*) / {username}   - 用户名\n"
        "(*rand*) / {rand_name}      - 随机称呼\n"
        "(*group*) / {chat_title}    - 当前群名\n"
        "(*groupid*) / {chat_id}     - 当前群ID\n\n"
        "示例：\n"
        "欢迎 (*name*) 来到\n"
        "(*group*)\n"
        "群ID\n"
        "群ID-(*groupid*)"
    )


@dp.callback_query(F.data.startswith("wl_img_"))
async def wl_img(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    wid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "wl_edit_image")
    await set_temp(uid, {"id": wid})
    await c.message.answer("请发送图片，或输入图片 URL / file_id：")


@dp.callback_query(F.data.startswith("wl_btn_"))
async def wl_btn(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    wid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "wl_edit_button")
    await set_temp(uid, {"id": wid})
    await c.message.answer(
        "按钮格式：\n"
        "新币供需 - https://t.me/gqdh && 新币公群 - https://t.me/gqdh\n"
        "每一行代表一排按钮。\n\n"
        "如果留空，系统会自动使用默认2个按钮。"
    )


@dp.callback_query(F.data.startswith("wl_delmin_"))
async def wl_delmin(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    wid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "wl_edit_delete_after")
    await set_temp(uid, {"id": wid})
    await c.message.answer("请输入删除消息的分钟数（0 = 不删除）：")


@dp.callback_query(F.data.startswith("wl_pre_"))
async def wl_pre(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    wid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        w = await db.get(WelcomeSetting, wid)
    if not w:
        return await c.message.answer("不存在。")
    preview_text = build_welcome_text(
        template=w.text or "欢迎 (*name*) 来到\n(*group*)\n群ID-(*groupid*)",
        user=c.from_user,
        chat_title="测试群组",
        chat_id="-1001234567890"
    )
    await send_preview(
        chat_id=c.from_user.id,
        text_=preview_text,
        image=w.image,
        button=(w.button or "").strip() or default_welcome_button_text(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("wl_del_"))
async def wl_del(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await ack(c, "已删除")
    wid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        await db.execute(delete(WelcomeSetting).where(WelcomeSetting.id == wid))
        await db.commit()
    await show_wl_list(c.message)


@dp.callback_query(F.data.startswith("wl_pin_"))
async def wl_pin(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    wid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        w = await db.get(WelcomeSetting, wid)
        if not w:
            return await c.message.answer("不存在。")
        w.pin = 0 if w.pin else 1
        await db.commit()
    await show_wl_view(c.message, wid)


# ======================
# AUTO MENU
# ======================

@dp.callback_query(F.data == "auto_menu")
async def auto_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await safe_edit(c.message, "📅 定时发送", reply_markup=auto_menu_kb())


@dp.callback_query(F.data == "auto_add")
async def auto_add(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "auto_add_chat")
    await set_temp(uid, {})
    await c.message.answer("请输入要创建定时发送的 chat_id：")


@dp.callback_query(F.data == "auto_list")
async def auto_list(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await show_auto_list(c.message)


@dp.callback_query(F.data.startswith("auto_view_"))
async def auto_view(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await show_auto_view(c.message, int(c.data.split("_")[-1]))


@dp.callback_query(F.data.startswith("auto_toggle_"))
async def auto_toggle(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    pid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)
        if not p:
            return await c.message.answer("未找到定时发送。")
        p.active = 0 if p.active else 1
        await db.commit()
    await show_auto_view(c.message, pid)


@dp.callback_query(F.data.startswith("auto_pin_"))
async def auto_pin(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    pid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)
        if not p:
            return await c.message.answer("未找到定时发送。")
        p.pin = 0 if p.pin else 1
        await db.commit()
    await show_auto_view(c.message, pid)


@dp.callback_query(F.data.startswith("auto_text_"))
async def auto_text(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "auto_edit_text")
    await set_temp(uid, {"id": pid})
    await c.message.answer("请输入文本内容：")


@dp.callback_query(F.data.startswith("auto_img_"))
async def auto_img(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "auto_edit_image")
    await set_temp(uid, {"id": pid})
    await c.message.answer("请发送图片，或输入图片 URL / file_id：")


@dp.callback_query(F.data.startswith("auto_btn_"))
async def auto_btn(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "auto_edit_button")
    await set_temp(uid, {"id": pid})
    await c.message.answer(
        "按钮格式：\n"
        "Google - https://google.com && YouTube - https://youtube.com\n"
        "每一行代表一排按钮。"
    )


@dp.callback_query(F.data.startswith("auto_chat_"))
async def auto_chat(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "auto_edit_chat")
    await set_temp(uid, {"id": pid})
    await c.message.answer("请输入新的 chat_id：")


@dp.callback_query(F.data.startswith("auto_int_"))
async def auto_int(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "auto_edit_interval")
    await set_temp(uid, {"id": pid})
    await c.message.answer("请输入重复间隔（分钟）：")


@dp.callback_query(F.data.startswith("auto_start_"))
async def auto_start(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "auto_edit_start")
    await set_temp(uid, {"id": pid})
    await c.message.answer("请输入开始时间：YYYY-MM-DD HH:MM")


@dp.callback_query(F.data.startswith("auto_end_"))
async def auto_end(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    pid = int(c.data.split("_")[-1])
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "auto_edit_end")
    await set_temp(uid, {"id": pid})
    await c.message.answer("请输入结束时间：YYYY-MM-DD HH:MM")


@dp.callback_query(F.data.startswith("auto_pre_"))
async def auto_pre(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    pid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        p = await db.get(AutoPost, pid)
    if not p:
        return await c.message.answer("不存在。")
    await send_preview(chat_id=c.from_user.id, text_=p.text, image=p.image, button=p.button)


@dp.callback_query(F.data.startswith("auto_del_"))
async def auto_del(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await ack(c, "已删除")
    pid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        await db.execute(delete(AutoPost).where(AutoPost.id == pid))
        await db.commit()
    await show_auto_list(c.message)


# ======================
# BANNED WORD MENU
# ======================

@dp.callback_query(F.data == "ban_menu")
async def ban_menu(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await safe_edit(c.message, "🚫 禁词管理", reply_markup=ban_menu_kb())


@dp.callback_query(F.data == "ban_add")
async def ban_add(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    uid = c.from_user.id
    await reset(uid)
    await set_state(uid, "ban_add")
    await set_temp(uid, {})
    await c.message.answer("请输入禁词，每行一个：")


@dp.callback_query(F.data == "ban_list")
async def ban_list(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    await show_banned_list(c.message)


@dp.callback_query(F.data.startswith("ban_del_"))
async def ban_del(c: types.CallbackQuery):
    if not await allowed_or_ignore(c):
        return
    bid = int(c.data.split("_")[-1])
    async with SessionLocal() as db:
        await db.execute(delete(BannedWord).where(BannedWord.id == bid))
        await db.commit()
    await reload_banned_cache()
    await show_banned_list(c.message)


@dp.callback_query(F.data == "ban_noop")
async def ban_noop(c: types.CallbackQuery):
    await ack(c)


# ======================
# QUICK ACTIONS
# ======================

@dp.message(F.text.regexp(r"(?i)^ghimmes$"))
async def pin_replied_message(m: types.Message):
    if m.chat.type not in ("group", "supergroup"):
        return await m.answer("该命令只能在群组中使用。")

    if not m.reply_to_message:
        return await m.answer("请回复要置顶的消息，然后发送 ghimmes")

    if not await is_chat_admin(bot, m.chat.id, m.from_user.id):
        return await m.answer("❌ 只有群管理员可以使用。")

    try:
        await bot.pin_chat_message(
            chat_id=m.chat.id,
            message_id=m.reply_to_message.message_id,
            disable_notification=False
        )
        with contextlib.suppress(Exception):
            await m.delete()
    except Exception as e:
        await m.answer(f"❌ 置顶失败：{e}")


@dp.message(F.text.regexp(r"^\s*担保表单"))
async def auto_rename_from_form(m: types.Message):
    if not m.from_user or not m.text:
        return

    if m.chat.type not in ("group", "supergroup"):
        return await m.answer("该功能只能在群组中使用。")

    if not await is_chat_admin(bot, m.chat.id, m.from_user.id):
        return await m.answer("❌ 只有群管理员可以提交担保表单。")

    data = parse_guarantee_form(m.text)
    required = ["组别", "名字", "编号", "规则"]
    missing = [x for x in required if not data.get(x)]
    if missing:
        return await m.answer("❌ 缺少字段：" + "，".join(missing))

    new_title = build_group_title_from_form(data)

    try:
        await bot.set_chat_title(chat_id=m.chat.id, title=new_title)

        async with SessionLocal() as db:
            row = (await db.execute(
                select(BotGroup).where(BotGroup.chat_id == str(m.chat.id))
            )).scalars().first()
            if row:
                row.title = new_title
                row.updated_at = int(time.time())
                await db.commit()

        await m.answer(f"担保规则写入成功\n{new_title}")
    except Exception as e:
        await m.answer(f"❌ 修改群名失败：{e}")


# ======================
# WEB ADMIN
# ======================

@app.get("/admin", response_class=HTMLResponse, name="admin_root")
async def admin_root(request: Request, key: str = ""):
    if web_authenticated(key):
        return web_redirect(request, "dashboard", key=key)
    return templates.TemplateResponse(
        "login.html",
        web_base_context(request, active_page="login", admin_key="")
    )


@app.get("/admin/login", response_class=HTMLResponse, name="login")
async def admin_login_page(request: Request, key: str = ""):
    if web_authenticated(key):
        return web_redirect(request, "dashboard", key=key)
    return templates.TemplateResponse(
        "login.html",
        web_base_context(request, active_page="login", admin_key="")
    )


@app.post("/admin/login", response_class=HTMLResponse, name="login_post")
async def admin_login_submit(request: Request, key: str = Form("")):
    if web_authenticated(key):
        return web_redirect(request, "dashboard", key=key)
    return templates.TemplateResponse(
        "login.html",
        web_base_context(
            request,
            active_page="login",
            admin_key="",
            error="Sai WEB_ADMIN_KEY hoặc key không hợp lệ."
        ),
        status_code=401
    )


@app.get("/admin/logout", response_class=HTMLResponse, name="logout")
async def admin_logout(request: Request, key: str = ""):
    return web_redirect(request, "login")


@app.get("/admin/dashboard", response_class=HTMLResponse, name="dashboard")
async def admin_dashboard(request: Request, key: str = ""):
    if not web_authenticated(key):
        return web_redirect(request, "login")

    admins, groups, keywords, welcomes, autos = await fetch_web_data()
    allowed_ids = allowed_admin_ids()

    stats = [
        {"label": "Allowed admins", "value": str(len(allowed_ids)), "note": "OWNER + ENV + DB", "icon": "bi-people-fill", "color": "primary"},
        {"label": "Groups", "value": str(len(groups)), "note": "Tracked groups", "icon": "bi-collection", "color": "success"},
        {"label": "Keywords", "value": str(len(keywords)), "note": "Auto reply rules", "icon": "bi-lightning-charge-fill", "color": "warning"},
        {"label": "Auto posts", "value": str(len(autos)), "note": "Scheduled tasks", "icon": "bi-calendar2-check-fill", "color": "danger"},
    ]

    now_str = datetime.now().strftime("%H:%M")
    recent_logs = [
        {"time": now_str, "user": "system", "action": "Webhook initialized", "status": "Ready", "badge_class": "bg-success"},
        {"time": now_str, "user": "system", "action": "Database schema loaded", "status": "OK", "badge_class": "bg-primary"},
        {"time": now_str, "user": "system", "action": "Admin cache loaded", "status": "OK", "badge_class": "bg-success"},
        {"time": now_str, "user": "system", "action": f"Welcome rules: {len(welcomes)}", "status": "Live", "badge_class": "bg-warning text-dark"},
    ]

    system_info = [
        {"label": "Bot username", "value": BOT_USERNAME},
        {"label": "Owner ID", "value": str(OWNER_ID) if OWNER_ID else "-"},
        {"label": "Admins allowed", "value": str(len(allowed_ids))},
        {"label": "Groups", "value": str(len(groups))},
        {"label": "Keywords", "value": str(len(keywords))},
        {"label": "Welcome rules", "value": str(len(welcomes))},
        {"label": "OpenAI", "value": "Enabled" if openai_client else "Disabled"},
        {"label": "BASE_URL", "value": BASE_URL},
    ]

    return templates.TemplateResponse(
        "dashboard.html",
        web_base_context(
            request,
            active_page="dashboard",
            admin_key=key,
            stats=stats,
            recent_logs=recent_logs,
            system_info=system_info,
        )
    )


@app.get("/admin/logs", response_class=HTMLResponse, name="logs")
async def admin_logs(request: Request, key: str = ""):
    if not web_authenticated(key):
        return web_redirect(request, "login")
    return templates.TemplateResponse(
        "logs.html",
        web_base_context(request, active_page="logs", admin_key=key)
    )


@app.get("/admin/admins", response_class=HTMLResponse, name="admins")
async def admin_admins(request: Request, key: str = ""):
    if not web_authenticated(key):
        return web_redirect(request, "login")

    admins, groups, keywords, welcomes, autos = await fetch_web_data()
    return templates.TemplateResponse(
        "admins.html",
        web_base_context(request, active_page="admins", admin_key=key, admins=admins)
    )


@app.post("/admin/admins/add", response_class=HTMLResponse, name="admins_add")
async def admin_admins_add(
    request: Request,
    user_id: int = Form(...),
    note: str = Form(""),
    key: str = Form("")
):
    if not web_authenticated(key):
        return web_redirect(request, "login")

    if user_id == OWNER_ID:
        raise HTTPException(status_code=400, detail="不能添加 OWNER")

    async with SessionLocal() as db:
        exists = (await db.execute(
            select(AdminUser).where(AdminUser.user_id == user_id)
        )).scalars().first()

        if exists:
            exists.note = note.strip()
        else:
            db.add(AdminUser(
                user_id=user_id,
                note=note.strip(),
                created_at=int(time.time())
            ))
        await db.commit()

    await load_admin_cache()
    return web_redirect(request, "admins", key=key)


@app.post("/admin/admins/delete", response_class=HTMLResponse, name="admins_delete")
async def admin_admins_delete(
    request: Request,
    user_id: int = Form(...),
    key: str = Form("")
):
    if not web_authenticated(key):
        return web_redirect(request, "login")

    if user_id == OWNER_ID:
        raise HTTPException(status_code=400, detail="不能删除 OWNER")

    async with SessionLocal() as db:
        await db.execute(delete(AdminUser).where(AdminUser.user_id == user_id))
        await db.commit()

    await load_admin_cache()
    return web_redirect(request, "admins", key=key)


@app.get("/admin/groups", response_class=HTMLResponse, name="groups")
async def admin_groups(request: Request, key: str = ""):
    if not web_authenticated(key):
        return web_redirect(request, "login")

    groups = await get_all_groups()
    return templates.TemplateResponse(
        "groups.html",
        web_base_context(request, active_page="groups", admin_key=key, groups=groups)
    )


@app.get("/admin/keywords", response_class=HTMLResponse, name="keywords")
async def admin_keywords(request: Request, key: str = ""):
    if not web_authenticated(key):
        return web_redirect(request, "login")

    async with SessionLocal() as db:
        keywords = (await db.execute(select(Keyword).order_by(Keyword.id.desc()))).scalars().all()

    return templates.TemplateResponse(
        "keywords.html",
        web_base_context(request, active_page="keywords", admin_key=key, keywords=keywords)
    )


@app.get("/admin/welcome", response_class=HTMLResponse, name="welcome")
async def admin_welcome(request: Request, key: str = ""):
    if not web_authenticated(key):
        return web_redirect(request, "login")

    async with SessionLocal() as db:
        welcomes = (await db.execute(select(WelcomeSetting).order_by(WelcomeSetting.id.desc()))).scalars().all()

    return templates.TemplateResponse(
        "welcome.html",
        web_base_context(request, active_page="welcome", admin_key=key, welcomes=welcomes)
    )


@app.get("/admin/auto", response_class=HTMLResponse, name="auto")
async def admin_auto(request: Request, key: str = ""):
    if not web_authenticated(key):
        return web_redirect(request, "login")

    async with SessionLocal() as db:
        autos = (await db.execute(select(AutoPost).order_by(AutoPost.id.desc()))).scalars().all()

    return templates.TemplateResponse(
        "auto.html",
        web_base_context(request, active_page="auto", admin_key=key, autos=autos)
    )


# ======================
# STATE HANDLER
# ======================

@dp.message()
async def all_messages(m: types.Message):
    if not m.from_user:
        return

    uid = m.from_user.id
    state = await get_state(uid)
    text_ = (m.text or m.caption or "").strip()
    is_admin_user = is_allowed_user(uid)

    if m.chat.type == "private" and not is_admin_user:
        if not text_ or text_.startswith("/"):
            return
        await m.answer(
            STRANGER_TEXT,
            parse_mode="HTML",
            disable_web_page_preview=False
        )
        return

    if text_ == "/start":
        await reset(uid)
        return await start(m)

    if text_ == "/cancel":
        await reset(uid)
        if is_admin_user:
            return await m.answer("已取消操作。", reply_markup=start_menu_kb(uid))
        return

    if state or (m.text and m.text.startswith("/")):
        print(f"[MESSAGE] chat={m.chat.id} user={uid} text={m.text!r} state={state}")

    # ---- AI ----
    if is_admin_user and state == "ai_prompt":
        if not openai_client:
            await reset(uid)
            return await m.answer(await t(uid, "ai_missing"), reply_markup=start_menu_kb(uid))

        prompt = (m.text or m.caption or "").strip()
        if not prompt:
            return await m.answer("请输入文本。")

        try:
            await m.answer(await t(uid, "ai_wait"))

            sys_prompt = (
                "请用中文简洁回答。"
                if await get_lang(uid) == "zh"
                else "Hãy trả lời bằng tiếng Việt ngắn gọn, rõ ràng."
            )

            resp = await openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7
            )

            answer = resp.choices[0].message.content or " "
            await reset(uid)
            return await m.answer(answer, reply_markup=start_menu_kb(uid))
        except Exception as e:
            print("[OPENAI ERROR]", e)
            await reset(uid)
            return await m.answer(await t(uid, "ai_error"), reply_markup=start_menu_kb(uid))

    # ---- AI AGENT ----
    if is_admin_user and state == "ai_agent":
        user_text = (m.text or m.caption or "").strip()
        if not user_text:
            return await m.answer("请输入你的要求。")

        result = await ai_agent_parse(user_text)
        action = result.get("action", "help")
        params = result.get("params", {}) or {}

        if result.get("need_more"):
            await set_temp(uid, {
                "pending_ai_action": action,
                "pending_ai_params": params
            })
            await set_state(uid, "ai_agent_wait_more")
            return await m.answer(result.get("question") or "请补充更多信息。")

        if action == "help":
            reply = result.get("reply") or "请直接说你要做什么。"
            return await m.answer(reply)

        if AI_AGENT_CONFIRM_REQUIRED and result.get("need_confirm"):
            await set_temp(uid, {
                "pending_ai_action": action,
                "pending_ai_params": params
            })
            await set_state(uid, "ai_agent_confirm")
            return await m.answer(
                f"⚠️ 即将执行操作：{action}\n"
                f"参数：{params}\n\n"
                f"请输入 确认 执行，或 /cancel 取消。"
            )

        return await execute_ai_action(m, uid, action, params)

    if is_admin_user and state == "ai_agent_wait_more":
        answer_text = (m.text or m.caption or "").strip()
        if not answer_text:
            return await m.answer("请继续输入。")

        tmp = await get_temp(uid)
        action = tmp.get("pending_ai_action")
        params = tmp.get("pending_ai_params", {}) or {}

        if action == "rename_group":
            params["title"] = answer_text
        elif action == "add_keyword":
            params["key"] = answer_text
        elif action == "set_welcome_text":
            params["text"] = answer_text
        elif action == "set_welcome_button":
            params["button"] = answer_text
        else:
            params["text"] = answer_text

        if AI_AGENT_CONFIRM_REQUIRED and action in ("rename_group", "lock_group", "unlock_group"):
            await set_temp(uid, {
                "pending_ai_action": action,
                "pending_ai_params": params
            })
            await set_state(uid, "ai_agent_confirm")
            return await m.answer(
                f"⚠️ 即将执行操作：{action}\n"
                f"参数：{params}\n\n"
                f"请输入 确认 执行，或 /cancel 取消。"
            )

        await set_state(uid, "ai_agent")
        await set_temp(uid, {})
        return await execute_ai_action(m, uid, action, params)

    if is_admin_user and state == "ai_agent_confirm":
        text_in = (m.text or "").strip()
        if text_in not in ("确认", "confirm", "yes", "ok"):
            return await m.answer("未确认执行。请输入 确认，或 /cancel 取消。")

        tmp = await get_temp(uid)
        action = tmp.get("pending_ai_action")
        params = tmp.get("pending_ai_params", {}) or {}

        await set_state(uid, "ai_agent")
        await set_temp(uid, {})
        return await execute_ai_action(m, uid, action, params)

    # ---- ADMIN ADD ID ----
    if is_admin_user and state == "admin_add_id":
        if uid != OWNER_ID:
            await reset(uid)
            return await m.answer("❌ 只有 OWNER 可以添加管理员。")

        raw = (m.text or "").strip()
        user_id = extract_first_int(raw)

        if not user_id:
            return await m.answer("请输入有效的 user_id。")

        if user_id == OWNER_ID:
            return await m.answer("不能添加 OWNER。")

        async with SessionLocal() as db:
            exists = (await db.execute(
                select(AdminUser).where(AdminUser.user_id == user_id)
            )).scalars().first()

            if exists:
                await reset(uid)
                await m.answer(f"该用户已是管理员：{user_id}")
                return await show_admin_user_list(m)

            db.add(AdminUser(
                user_id=user_id,
                note="added_from_bot_menu",
                created_at=int(time.time())
            ))
            await db.commit()

        await load_admin_cache()
        await reset(uid)
        await m.answer(f"✅ 已添加管理员：{user_id}")
        return await show_admin_user_list(m)

    # ---- KEYWORD ----
    if is_admin_user and state == "kw_add_key":
        raw = (m.text or "").strip()
        if not raw:
            return await m.answer("关键词不能为空。")

        keys = [line.strip() for line in raw.splitlines() if line.strip()]
        if not keys:
            return await m.answer("关键词不能为空。")

        if len(keys) > 1:
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

            await reload_keyword_cache()
            await reset(uid)
            await m.answer(f"已添加 {added} 个关键词。\n已跳过 {existed} 个已存在关键词。")
            return await show_kw_list(m)

        key = keys[0]
        async with SessionLocal() as db:
            exists = (await db.execute(select(Keyword).where(Keyword.key == key))).scalars().first()
            if exists:
                return await m.answer("关键词已存在，请输入其他关键词。")

            row = Keyword(key=key, mode="exact", active=1, text="", image="", button="")
            db.add(row)
            await db.commit()
            await db.refresh(row)

        await reload_keyword_cache()
        await set_state(uid, "kw_add_text")
        await set_temp(uid, {"id": row.id})
        return await m.answer("已保存关键词。\n请输入回复文本，若跳过请输入 skip")

    if is_admin_user and state == "kw_add_text":
        tmp = await get_temp(uid)
        kid = tmp["id"]
        value = (m.text or "").strip()

        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if not k:
                await reset(uid)
                return await m.answer("关键词不存在。")

            if value.lower() != "skip":
                k.text = value
                await db.commit()

        await reload_keyword_cache()
        await set_state(uid, "kw_add_image")
        await set_temp(uid, {"id": kid})
        return await m.answer("请发送图片，或输入图片 URL / file_id。\n若跳过请输入 skip")

    if is_admin_user and state == "kw_add_image":
        tmp = await get_temp(uid)
        kid = tmp["id"]
        raw = (m.text or "").strip().lower()

        image = None if raw == "skip" else extract_image_from_message(m)
        if raw != "skip" and not image:
            return await m.answer("请发送图片，或输入图片 URL / file_id，若跳过请输入 skip")

        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if not k:
                await reset(uid)
                return await m.answer("关键词不存在。")
            if image:
                k.image = image
                await db.commit()

        await reload_keyword_cache()
        await set_state(uid, "kw_add_button")
        await set_temp(uid, {"id": kid})
        return await m.answer(
            "请输入按钮。\n"
            "格式：Google - https://google.com && YouTube - https://youtube.com\n"
            "每一行代表一排按钮。\n"
            "若跳过请输入 skip"
        )

    if is_admin_user and state == "kw_add_button":
        tmp = await get_temp(uid)
        kid = tmp["id"]
        value = (m.text or "").strip()

        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if not k:
                await reset(uid)
                return await m.answer("关键词不存在。")

            if value.lower() != "skip":
                k.button = value
                await db.commit()

        await reload_keyword_cache()
        await reset(uid)
        await m.answer("关键词创建完成。")
        return await show_kw_view(m, kid)

    if is_admin_user and state == "kw_edit_key":
        tmp = await get_temp(uid)
        kid = tmp["id"]
        key = (m.text or "").strip()
        if not key:
            return await m.answer("关键词不能为空。")

        async with SessionLocal() as db:
            exists = (await db.execute(
                select(Keyword).where(Keyword.key == key, Keyword.id != kid)
            )).scalars().first()
            if exists:
                return await m.answer("关键词已存在，请输入其他关键词。")

            k = await db.get(Keyword, kid)
            if k:
                k.key = key
                await db.commit()

        await reload_keyword_cache()
        await reset(uid)
        await m.answer("关键词已更新。")
        return await show_kw_view(m, kid)

    if is_admin_user and state == "kw_edit_text":
        tmp = await get_temp(uid)
        kid = tmp["id"]
        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if k:
                k.text = m.text or ""
                await db.commit()
        await reload_keyword_cache()
        await reset(uid)
        await m.answer("文本已更新。")
        return await show_kw_view(m, kid)

    if is_admin_user and state == "kw_edit_image":
        tmp = await get_temp(uid)
        kid = tmp["id"]
        image = extract_image_from_message(m)
        if not image:
            return await m.answer("请发送图片，或输入图片 URL / file_id。")
        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if k:
                k.image = image
                await db.commit()
        await reload_keyword_cache()
        await reset(uid)
        await m.answer("媒体已更新。")
        return await show_kw_view(m, kid)

    if is_admin_user and state == "kw_edit_button":
        tmp = await get_temp(uid)
        kid = tmp["id"]
        async with SessionLocal() as db:
            k = await db.get(Keyword, kid)
            if k:
                k.button = m.text or ""
                await db.commit()
        await reload_keyword_cache()
        await reset(uid)
        await m.answer("按钮已更新。")
        return await show_kw_view(m, kid)

    # ---- BANNED WORDS ----
    if is_admin_user and state == "ban_add":
        raw = (m.text or "").strip()
        words = [x.strip().lower() for x in raw.splitlines() if x.strip()]
        if not words:
            return await m.answer("禁词不能为空。")

        added = 0
        existed = 0

        async with SessionLocal() as db:
            for word in words:
                row = (await db.execute(select(BannedWord).where(BannedWord.word == word))).scalars().first()
                if row:
                    existed += 1
                    continue
                db.add(BannedWord(word=word))
                added += 1
            await db.commit()

        await reload_banned_cache()
        await reset(uid)
        await m.answer(f"已添加 {added} 个禁词，跳过 {existed} 个已存在禁词。")
        return await show_banned_list(m)

    # ---- GROUP RENAME ----
    if is_admin_user and state == "group_rename":
        tmp = await get_temp(uid)
        chat_id = tmp["chat_id"]
        new_title = (m.text or "").strip()
        if not new_title:
            return await m.answer("群名不能为空。")

        try:
            await bot.set_chat_title(chat_id=chat_id, title=new_title[:128])
            async with SessionLocal() as db:
                row = (await db.execute(select(BotGroup).where(BotGroup.chat_id == str(chat_id)))).scalars().first()
                if row:
                    row.title = new_title[:128]
                    row.updated_at = int(time.time())
                    await db.commit()
            await reset(uid)
            return await m.answer(f"✅ 群名已更新：{new_title}", reply_markup=start_menu_kb(uid))
        except Exception as e:
            await reset(uid)
            return await m.answer(f"❌ 修改群名失败：{e}", reply_markup=start_menu_kb(uid))

    # ---- WELCOME ----
    if is_admin_user and state == "wl_add_chat":
        chat_id = (m.text or "").strip()
        if not chat_id:
            return await m.answer("chat_id 不能为空。")
        async with SessionLocal() as db:
            exists = (await db.execute(select(WelcomeSetting).where(WelcomeSetting.chat_id == chat_id))).scalars().first()
            if exists:
                return await m.answer("该 chat_id 已存在。")
            db.add(WelcomeSetting(chat_id=chat_id))
            await db.commit()
        await reset(uid)
        return await m.answer("欢迎配置已创建。", reply_markup=start_menu_kb(uid))

    if is_admin_user and state == "wl_edit_text":
        tmp = await get_temp(uid)
        wid = tmp["id"]
        async with SessionLocal() as db:
            w = await db.get(WelcomeSetting, wid)
            if w:
                w.text = m.text or ""
                await db.commit()
        await reset(uid)
        return await m.answer("欢迎文本已更新。", reply_markup=start_menu_kb(uid))

    if is_admin_user and state == "wl_edit_image":
        tmp = await get_temp(uid)
        wid = tmp["id"]
        image = extract_image_from_message(m)
        if not image:
            return await m.answer("请发送图片，或输入图片 URL / file_id。")
        async with SessionLocal() as db:
            w = await db.get(WelcomeSetting, wid)
            if w:
                w.image = image
                await db.commit()
        await reset(uid)
        return await m.answer("媒体已更新。", reply_markup=start_menu_kb(uid))

    if is_admin_user and state == "wl_edit_button":
        tmp = await get_temp(uid)
        wid = tmp["id"]
        async with SessionLocal() as db:
            w = await db.get(WelcomeSetting, wid)
            if w:
                w.button = m.text or ""
                await db.commit()
        await reset(uid)
        return await m.answer("按钮已更新。", reply_markup=start_menu_kb(uid))

    if is_admin_user and state == "wl_edit_delete_after":
        tmp = await get_temp(uid)
        wid = tmp["id"]
        try:
            minutes = int((m.text or "").strip())
            if minutes < 0:
                raise ValueError
        except ValueError:
            return await m.answer("请输入大于等于 0 的整数。")
        async with SessionLocal() as db:
            w = await db.get(WelcomeSetting, wid)
            if w:
                w.delete_after = minutes
                await db.commit()
        await reset(uid)
        return await m.answer("删除时间已更新。", reply_markup=start_menu_kb(uid))

    # ---- AUTO ----
    if is_admin_user and state == "auto_add_chat":
        chat_id = (m.text or "").strip()
        if not chat_id:
            return await m.answer("chat_id 不能为空。")
        async with SessionLocal() as db:
            db.add(AutoPost(chat_id=chat_id))
            await db.commit()
        await reset(uid)
        return await m.answer("定时发送已创建。", reply_markup=start_menu_kb(uid))

    if is_admin_user and state == "auto_edit_text":
        tmp = await get_temp(uid)
        pid = tmp["id"]
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.text = m.text or ""
                await db.commit()
        await reset(uid)
        return await m.answer("文本已更新。", reply_markup=start_menu_kb(uid))

    if is_admin_user and state == "auto_edit_image":
        tmp = await get_temp(uid)
        pid = tmp["id"]
        image = extract_image_from_message(m)
        if not image:
            return await m.answer("请发送图片，或输入图片 URL / file_id。")
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.image = image
                await db.commit()
        await reset(uid)
        return await m.answer("媒体已更新。", reply_markup=start_menu_kb(uid))

    if is_admin_user and state == "auto_edit_button":
        tmp = await get_temp(uid)
        pid = tmp["id"]
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.button = m.text or ""
                await db.commit()
        await reset(uid)
        return await m.answer("按钮已更新。", reply_markup=start_menu_kb(uid))

    if is_admin_user and state == "auto_edit_chat":
        tmp = await get_temp(uid)
        pid = tmp["id"]
        chat_id = (m.text or "").strip()
        if not chat_id:
            return await m.answer("chat_id 无效。")
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.chat_id = chat_id
                await db.commit()
        await reset(uid)
        return await m.answer("chat_id 已更新。", reply_markup=start_menu_kb(uid))

    if is_admin_user and state == "auto_edit_interval":
        tmp = await get_temp(uid)
        pid = tmp["id"]
        try:
            interval = int((m.text or "").strip())
            if interval <= 0:
                raise ValueError
        except ValueError:
            return await m.answer("间隔时间必须是正整数。")
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.interval = interval
                await db.commit()
        await reset(uid)
        return await m.answer("间隔时间已更新。", reply_markup=start_menu_kb(uid))

    if is_admin_user and state == "auto_edit_start":
        tmp = await get_temp(uid)
        pid = tmp["id"]
        if not parse_dt(m.text or ""):
            return await m.answer("格式错误。请使用：YYYY-MM-DD HH:MM")
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.start_at = m.text.strip()
                await db.commit()
        await reset(uid)
        return await m.answer("开始时间已更新。", reply_markup=start_menu_kb(uid))

    if is_admin_user and state == "auto_edit_end":
        tmp = await get_temp(uid)
        pid = tmp["id"]
        if not parse_dt(m.text or ""):
            return await m.answer("格式错误。请使用：YYYY-MM-DD HH:MM")
        async with SessionLocal() as db:
            p = await db.get(AutoPost, pid)
            if p:
                p.end_at = m.text.strip()
                await db.commit()
        await reset(uid)
        return await m.answer("结束时间已更新。", reply_markup=start_menu_kb(uid))

    # ---- BANNED WORDS CHECK FOR ALL USERS ----
    if m.chat.type in ("group", "supergroup") and text_ and not text_.startswith("/"):
        lower_text = text_.lower()
        for row in banned_cache:
            word = (row.word or "").strip().lower()
            if word and word in lower_text:
                with contextlib.suppress(Exception):
                    await m.delete()
                return

    # ---- KEYWORD AUTO REPLY FOR ALL USERS ----
    if not text_ or text_.startswith("/"):
        return

    kws = keyword_cache
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
        await send_preview(
            chat_id=m.chat.id,
            text_=matched.text,
            image=matched.image,
            button=matched.button
        )


# ======================
# WELCOME NEW MEMBER
# ======================

@dp.message(F.new_chat_members)
async def welcome_new_member(m: types.Message):
    if not m.chat or not m.new_chat_members:
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

    current_chat_title = m.chat.title or ""

    for new_user in m.new_chat_members:
        try:
            welcome_text = build_welcome_text(
                template=w.text or "欢迎 (*name*) 来到\n(*group*)\n群ID\n群ID-(*groupid*)",
                user=new_user,
                chat_title=current_chat_title,
                chat_id=m.chat.id
            )

            btn_text = (w.button or "").strip() or default_welcome_button_text()

            msg = await send_preview(
                chat_id=m.chat.id,
                text_=welcome_text,
                image=w.image,
                button=btn_text,
                parse_mode="HTML"
            )

            if w.pin:
                with contextlib.suppress(Exception):
                    await bot.pin_chat_message(chat_id=m.chat.id, message_id=msg.message_id)

            if w.delete_after and w.delete_after > 0:
                async def later_delete(chat_id_, msg_id_, delay_):
                    await asyncio.sleep(delay_)
                    with contextlib.suppress(Exception):
                        await bot.delete_message(chat_id=chat_id_, message_id=msg_id_)

                asyncio.create_task(
                    later_delete(m.chat.id, msg.message_id, w.delete_after * 60)
                )

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
                    msg = await send_preview(chat_id=p.chat_id, text_=p.text, image=p.image, button=p.button)

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
    try:
        data = await req.json()
        update = types.Update.model_validate(data)

        print("[WEBHOOK]", {
            "update_id": data.get("update_id"),
            "has_message": "message" in data,
            "has_callback": "callback_query" in data,
            "has_my_chat_member": "my_chat_member" in data,
        })

        await dp.feed_update(bot, update)
        return {"ok": True}

    except Exception as e:
        print("[WEBHOOK ERROR]", repr(e))
        traceback.print_exc()
        return {"ok": False, "error": str(e)}


async def ensure_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def wait_for_redis(max_retries: int = 5, delay: int = 2):
    if not redis_client:
        print("[REDIS] disabled")
        return

    last_error = None
    for i in range(max_retries):
        try:
            await redis_client.ping()
            print("[REDIS] connected")
            return
        except Exception as e:
            last_error = e
            print(f"[REDIS] connect failed ({i+1}/{max_retries}): {e}")
            await asyncio.sleep(delay)

    print("[REDIS] unavailable, fallback to RAM")

@app.on_event("startup")
async def startup():
    global worker_task

    print("[STARTUP] begin")

    try:
        print("[STARTUP] wait_for_redis...")
        await wait_for_redis()
        print("[STARTUP] wait_for_redis done")

        print("[STARTUP] wait_for_db...")
        await wait_for_db()
        print("[STARTUP] wait_for_db OK")

        print("[STARTUP] ensure_schema...")
        await ensure_schema()
        print("[STARTUP] ensure_schema OK")

        print("[STARTUP] load_admin_cache...")
        await load_admin_cache()
        print("[STARTUP] load_admin_cache OK")

        print("[STARTUP] reload_keyword_cache...")
        await reload_keyword_cache()
        print("[STARTUP] reload_keyword_cache OK")

        print("[STARTUP] reload_banned_cache...")
        await reload_banned_cache()
        print("[STARTUP] reload_banned_cache OK")

        worker_task = asyncio.create_task(auto_worker())

        webhook_url = f"{BASE_URL}/webhook"
        print("[STARTUP] BASE_URL =", BASE_URL)
        print("[STARTUP] webhook_url =", webhook_url)

        await bot.delete_webhook(drop_pending_updates=True)
        print("[STARTUP] old webhook deleted")

        result = await bot.set_webhook(
            webhook_url,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query", "my_chat_member"]
        )
        print("[STARTUP] set_webhook result =", result)

        info = await bot.get_webhook_info()
        print("[WEBHOOK INFO URL]", info.url)
        print("[WEBHOOK INFO PENDING]", info.pending_update_count)
        print("[WEBHOOK INFO LAST ERROR]", info.last_error_message)

        print("READY")

    except Exception as e:
        print("[STARTUP ERROR]", repr(e))
        traceback.print_exc()
        raise


@app.on_event("shutdown")
async def shutdown():
    global worker_task

    if worker_task:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task

    with contextlib.suppress(Exception):
        await bot.delete_webhook()

    with contextlib.suppress(Exception):
        if redis_client:
            await redis_client.close()

    await bot.session.close()


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
