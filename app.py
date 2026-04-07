

```

---

# 4. `app/core/config.py`

```python
import os
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> Tuple[str, dict]:
    connect_args = {}

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))

    sslmode = query.pop("sslmode", None)
    if sslmode == "require":
        connect_args["ssl"] = "require"

    new_query = urlencode(query)
    url = urlunparse(parsed._replace(query=new_query))

    return url, connect_args


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    BOT_TOKEN: str
    BASE_URL: str
    DATABASE_URL: str

    REDIS_URL: Optional[str] = None

    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_SYSTEM_PROMPT: str = "You are a helpful Telegram assistant."

    ADMIN_WEB_KEY: str
    SESSION_SECRET: str
    TELEGRAM_WEBHOOK_SECRET: str = ""

    PORT: int = 8000

    @property
    def base_url(self) -> str:
        return self.BASE_URL.rstrip("/")

    @property
    def webhook_url(self) -> str:
        return f"{self.base_url}/webhook"

    @property
    def db_url_and_args(self):
        return normalize_database_url(self.DATABASE_URL)


settings = Settings()
```

---

# 5. `app/db/base.py`

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

---

# 6. `app/db/models.py`

```python
from datetime import datetime
from sqlalchemy import BigInteger, String, Text, DateTime, Integer, Boolean, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BotGroup(Base):
    __tablename__ = "bot_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(255), index=True)
    reply: Mapped[str] = mapped_column(Text)
    buttons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BannedWord(Base):
    __tablename__ = "banned_words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    word: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WelcomeSetting(Base):
    __tablename__ = "welcome_settings"
    __table_args__ = (UniqueConstraint("chat_id", name="uq_welcome_chat_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    text: Mapped[str] = mapped_column(Text)
    buttons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChatMemory(Base):
    __tablename__ = "chat_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

---

# 7. `app/db/session.py`

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.config import settings

DATABASE_URL, DB_CONNECT_ARGS = settings.db_url_and_args

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_timeout=60,
    connect_args=DB_CONNECT_ARGS,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
```

---

# 8. `app/core/cache.py`

```python
import json
from typing import Any, Optional

try:
    import redis.asyncio as redis
except Exception:
    redis = None

from app.core.config import settings


class MemoryFallbackCache:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> Optional[str]:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value

    async def delete(self, key: str):
        self.store.pop(key, None)


class Cache:
    def __init__(self):
        self.client = None
        self.fallback = MemoryFallbackCache()

    async def connect(self):
        if settings.REDIS_URL and redis:
            try:
                self.client = redis.from_url(settings.REDIS_URL, decode_responses=True)
                await self.client.ping()
                print("[CACHE] Redis connected")
            except Exception as e:
                print("[CACHE] Redis failed, fallback RAM:", repr(e))
                self.client = None
        else:
            print("[CACHE] Using RAM fallback")

    async def get_json(self, key: str, default: Any = None):
        raw = None
        if self.client:
            raw = await self.client.get(key)
        else:
            raw = await self.fallback.get(key)

        if raw is None:
            return default
        try:
            return json.loads(raw)
        except Exception:
            return default

    async def set_json(self, key: str, value: Any, ex: int | None = None):
        raw = json.dumps(value, ensure_ascii=False)
        if self.client:
            await self.client.set(key, raw, ex=ex)
        else:
            await self.fallback.set(key, raw, ex=ex)

    async def delete(self, key: str):
        if self.client:
            await self.client.delete(key)
        else:
            await self.fallback.delete(key)


cache = Cache()
```

---

# 9. `app/core/security.py`

```python
from fastapi import HTTPException, Request


def require_admin_session(request: Request):
    if not request.session.get("admin_ok"):
        raise HTTPException(status_code=401, detail="Unauthorized")
```

---

# 10. `app/services/group_service.py`

```python
from datetime import datetime
from sqlalchemy import select

from app.db.models import BotGroup
from app.db.session import SessionLocal


async def upsert_group(chat_id: int, title: str | None, username: str | None, is_admin: bool = False):
    async with SessionLocal() as db:
        row = (await db.execute(
            select(BotGroup).where(BotGroup.chat_id == chat_id)
        )).scalar_one_or_none()

        if row:
            row.title = title
            row.username = username
            row.is_admin = is_admin
            row.updated_at = datetime.utcnow()
        else:
            row = BotGroup(
                chat_id=chat_id,
                title=title,
                username=username,
                is_admin=is_admin,
                updated_at=datetime.utcnow(),
            )
            db.add(row)

        await db.commit()
        return row


async def set_group_ai(chat_id: int, enabled: bool):
    async with SessionLocal() as db:
        row = (await db.execute(
            select(BotGroup).where(BotGroup.chat_id == chat_id)
        )).scalar_one_or_none()
        if row:
            row.ai_enabled = enabled
            row.updated_at = datetime.utcnow()
            await db.commit()
        return row


async def get_group(chat_id: int):
    async with SessionLocal() as db:
        return (await db.execute(
            select(BotGroup).where(BotGroup.chat_id == chat_id)
        )).scalar_one_or_none()


async def get_all_groups():
    async with SessionLocal() as db:
        return (await db.execute(
            select(BotGroup).order_by(BotGroup.updated_at.desc())
        )).scalars().all()
```

---

# 11. `app/services/keyword_service.py`

```python
from sqlalchemy import select

from app.db.models import Keyword, BannedWord
from app.db.session import SessionLocal


async def get_keywords():
    async with SessionLocal() as db:
        return (await db.execute(
            select(Keyword).order_by(Keyword.id.desc())
        )).scalars().all()


async def find_keyword_reply(text: str):
    text_lower = text.lower().strip()
    async with SessionLocal() as db:
        rows = (await db.execute(select(Keyword))).scalars().all()
        for row in rows:
            if row.trigger.lower().strip() in text_lower:
                return row
    return None


async def get_banned_words():
    async with SessionLocal() as db:
        return (await db.execute(
            select(BannedWord).order_by(BannedWord.id.desc())
        )).scalars().all()


async def contains_banned_word(text: str):
    text_lower = text.lower()
    async with SessionLocal() as db:
        rows = (await db.execute(select(BannedWord))).scalars().all()
        for row in rows:
            if row.word.lower() in text_lower:
                return row.word
    return None
```

---

# 12. `app/services/welcome_service.py`

```python
from sqlalchemy import select

from app.db.models import WelcomeSetting
from app.db.session import SessionLocal


async def get_welcome(chat_id: int):
    async with SessionLocal() as db:
        return (await db.execute(
            select(WelcomeSetting).where(WelcomeSetting.chat_id == chat_id)
        )).scalar_one_or_none()


async def set_welcome(chat_id: int, text: str, enabled: bool = True, buttons=None):
    async with SessionLocal() as db:
        row = (await db.execute(
            select(WelcomeSetting).where(WelcomeSetting.chat_id == chat_id)
        )).scalar_one_or_none()

        if row:
            row.text = text
            row.enabled = enabled
            row.buttons = buttons
        else:
            row = WelcomeSetting(
                chat_id=chat_id,
                text=text,
                enabled=enabled,
                buttons=buttons,
            )
            db.add(row)

        await db.commit()
        return row
```

---

# 13. `app/services/openai_service.py`

```python
from sqlalchemy import select, delete
from openai import AsyncOpenAI

from app.core.config import settings
from app.db.models import ChatMemory
from app.db.session import SessionLocal

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None


async def save_chat_message(chat_id: int, user_id: int, role: str, content: str):
    async with SessionLocal() as db:
        db.add(ChatMemory(
            chat_id=chat_id,
            user_id=user_id,
            role=role,
            content=content,
        ))
        await db.commit()


async def get_recent_history(chat_id: int, user_id: int, limit: int = 10):
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(ChatMemory)
            .where(ChatMemory.chat_id == chat_id, ChatMemory.user_id == user_id)
            .order_by(ChatMemory.id.desc())
            .limit(limit)
        )).scalars().all()

    rows.reverse()
    return [{"role": r.role, "content": r.content} for r in rows]


async def trim_history(chat_id: int, user_id: int, keep_last: int = 20):
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(ChatMemory.id)
            .where(ChatMemory.chat_id == chat_id, ChatMemory.user_id == user_id)
            .order_by(ChatMemory.id.desc())
        )).scalars().all()

        if len(rows) > keep_last:
            remove_ids = rows[keep_last:]
            await db.execute(delete(ChatMemory).where(ChatMemory.id.in_(remove_ids)))
            await db.commit()


async def ask_openai(chat_id: int, user_id: int, user_text: str) -> str | None:
    if not client:
        return None

    history = await get_recent_history(chat_id, user_id, limit=8)

    messages = [
        {"role": "system", "content": settings.OPENAI_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": user_text},
    ]

    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=messages,
        temperature=0.7,
    )

    content = response.choices[0].message.content.strip() if response.choices else ""
    return content or None
```

---

# 14. `app/bot/keyboards.py`

```python
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_buttons(buttons: list | None):
    if not buttons:
        return None

    builder = InlineKeyboardBuilder()
    for item in buttons:
        text = item.get("text")
        url = item.get("url")
        if not text or not url:
            continue
        builder.button(text=text, url=url)

    builder.adjust(1)
    return builder.as_markup()
```

---

# 15. `app/bot/instance.py`

```python
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.core.config import settings

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
```

---

# 16. `app/bot/handlers.py`

```python
from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.types import Message, ChatMemberUpdated

from app.bot.keyboards import build_buttons
from app.services.group_service import upsert_group, get_group
from app.services.keyword_service import find_keyword_reply, contains_banned_word
from app.services.welcome_service import get_welcome
from app.services.openai_service import ask_openai, save_chat_message, trim_history

router = Router()


@router.message(F.text == "/start")
async def start_cmd(message: Message):
    await message.answer("Bot đang hoạt động.")


@router.my_chat_member()
async def track_bot_membership(event: ChatMemberUpdated):
    chat = event.chat
    new_status = event.new_chat_member.status
    is_admin = new_status in ("administrator", "creator")

    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        await upsert_group(
            chat_id=chat.id,
            title=chat.title,
            username=getattr(chat, "username", None),
            is_admin=is_admin,
        )


@router.message(F.new_chat_members)
async def welcome_new_members(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    setting = await get_welcome(message.chat.id)
    if not setting or not setting.enabled:
        return

    names = ", ".join([u.full_name for u in message.new_chat_members])
    text = setting.text.replace("{name}", names).replace("{group}", message.chat.title or "")
    await message.answer(text, reply_markup=build_buttons(setting.buttons))


@router.message(F.text)
async def text_handler(message: Message):
    text = message.text.strip()

    # lưu thông tin group nếu có
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        await upsert_group(
            chat_id=message.chat.id,
            title=message.chat.title,
            username=getattr(message.chat, "username", None),
            is_admin=False,
        )

    # banned words
    banned = await contains_banned_word(text)
    if banned:
        try:
            await message.delete()
            await message.answer(f"Tin nhắn bị xoá do chứa từ cấm: {banned}")
        except Exception:
            pass
        return

    # keyword reply
    keyword = await find_keyword_reply(text)
    if keyword:
        await message.answer(
            keyword.reply,
            reply_markup=build_buttons(keyword.buttons)
        )
        return

    # private chat -> cho AI trả lời
    if message.chat.type == ChatType.PRIVATE:
        await save_chat_message(message.chat.id, message.from_user.id, "user", text)
        ai_reply = await ask_openai(message.chat.id, message.from_user.id, text)
        if ai_reply:
            await save_chat_message(message.chat.id, message.from_user.id, "assistant", ai_reply)
            await trim_history(message.chat.id, message.from_user.id)
            await message.answer(ai_reply)
        return

    # group chat -> chỉ trả lời AI khi group bật AI
    group = await get_group(message.chat.id)
    if group and group.ai_enabled:
        await save_chat_message(message.chat.id, message.from_user.id, "user", text)
        ai_reply = await ask_openai(message.chat.id, message.from_user.id, text)
        if ai_reply:
            await save_chat_message(message.chat.id, message.from_user.id, "assistant", ai_reply)
            await trim_history(message.chat.id, message.from_user.id)
            await message.reply(ai_reply)
```

---

# 17. `app/web/admin.py`

Bản này dùng **session cookie**, không dùng `?key=...` nữa.

```python
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import select, func

from app.core.config import settings
from app.core.security import require_admin_session
from app.db.models import AdminUser, BotGroup, Keyword, BannedWord, WelcomeSetting
from app.db.session import SessionLocal
from app.services.group_service import set_group_ai

router = APIRouter(prefix="/admin", tags=["admin"])


def html_page(title: str, body: str):
    return HTMLResponse(f"""
    <html>
    <head>
      <title>{title}</title>
      <meta charset="utf-8" />
      <style>
        body {{ font-family: Arial; max-width: 1000px; margin: 30px auto; padding: 0 16px; }}
        input, textarea, button {{ width: 100%; padding: 10px; margin: 6px 0; }}
        .card {{ border: 1px solid #ddd; padding: 16px; margin: 12px 0; border-radius: 8px; }}
        .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
        pre {{ white-space: pre-wrap; background: #f6f6f6; padding: 10px; border-radius: 6px; }}
        a {{ text-decoration: none; }}
      </style>
    </head>
    <body>
      {body}
    </body>
    </html>
    """)


@router.get("/login")
async def login_page():
    return html_page("Admin Login", """
    <h2>Admin Login</h2>
    <form method="post">
      <input type="password" name="key" placeholder="Admin key" />
      <button type="submit">Login</button>
    </form>
    """)


@router.post("/login")
async def login_submit(request: Request, key: str = Form(...)):
    if key != settings.ADMIN_WEB_KEY:
        return html_page("Login Failed", "<h3>Sai key</h3><a href='/admin/login'>Thử lại</a>")

    request.session["admin_ok"] = True
    return RedirectResponse("/admin", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=302)


@router.get("")
async def admin_home(request: Request):
    require_admin_session(request)

    async with SessionLocal() as db:
        admins_count = await db.scalar(select(func.count(AdminUser.id)))
        groups_count = await db.scalar(select(func.count(BotGroup.id)))
        keywords_count = await db.scalar(select(func.count(Keyword.id)))
        banned_count = await db.scalar(select(func.count(BannedWord.id)))
        welcomes_count = await db.scalar(select(func.count(WelcomeSetting.id)))

        groups = (await db.execute(select(BotGroup).order_by(BotGroup.updated_at.desc()))).scalars().all()
        keywords = (await db.execute(select(Keyword).order_by(Keyword.id.desc()))).scalars().all()
        banned_words = (await db.execute(select(BannedWord).order_by(BannedWord.id.desc()))).scalars().all()

    groups_html = "".join([
        f"""
        <div class='card'>
          <b>{g.title or 'No title'}</b><br>
          chat_id: {g.chat_id}<br>
          is_admin: {g.is_admin}<br>
          ai_enabled: {g.ai_enabled}<br>
          <form method="post" action="/admin/groups/{g.chat_id}/ai">
            <input type="hidden" name="enabled" value="{str(not g.ai_enabled).lower()}">
            <button type="submit">Đổi AI => {not g.ai_enabled}</button>
          </form>
        </div>
        """ for g in groups
    ]) or "<p>Chưa có group</p>"

    keywords_html = "".join([
        f"<div class='card'><b>{k.trigger}</b><pre>{k.reply}</pre></div>" for k in keywords
    ]) or "<p>Chưa có keyword</p>"

    banned_html = "".join([
        f"<div class='card'><b>{b.word}</b></div>" for b in banned_words
    ]) or "<p>Chưa có banned words</p>"

    body = f"""
    <h2>Admin Dashboard</h2>
    <p><a href="/admin/logout">Logout</a></p>

    <div class="row">
      <div class="card">Admins: <b>{admins_count}</b></div>
      <div class="card">Groups: <b>{groups_count}</b></div>
      <div class="card">Keywords: <b>{keywords_count}</b></div>
      <div class="card">Banned words: <b>{banned_count}</b></div>
      <div class="card">Welcome configs: <b>{welcomes_count}</b></div>
    </div>

    <div class="row">
      <div class="card">
        <h3>Thêm keyword</h3>
        <form method="post" action="/admin/keywords">
          <input name="trigger" placeholder="Trigger" />
          <textarea name="reply" placeholder="Reply"></textarea>
          <textarea name="buttons" placeholder='Buttons JSON, ví dụ: [{{"text":"Site","url":"https://example.com"}}]'></textarea>
          <button type="submit">Lưu keyword</button>
        </form>
      </div>

      <div class="card">
        <h3>Thêm từ cấm</h3>
        <form method="post" action="/admin/banned">
          <input name="word" placeholder="Từ cấm" />
          <button type="submit">Lưu từ cấm</button>
        </form>
      </div>
    </div>

    <div class="card">
      <h3>Cấu hình welcome</h3>
      <form method="post" action="/admin/welcome">
        <input name="chat_id" placeholder="Chat ID group" />
        <textarea name="text" placeholder="Welcome text, dùng {{name}} và {{group}}"></textarea>
        <textarea name="buttons" placeholder='Buttons JSON'></textarea>
        <input name="enabled" placeholder="true / false" value="true" />
        <button type="submit">Lưu welcome</button>
      </form>
    </div>

    <h3>Groups</h3>
    {groups_html}

    <h3>Keywords</h3>
    {keywords_html}

    <h3>Banned words</h3>
    {banned_html}
    """
    return html_page("Dashboard", body)


@router.post("/keywords")
async def create_keyword(
    request: Request,
    trigger: str = Form(...),
    reply: str = Form(...),
    buttons: str = Form("")
):
    require_admin_session(request)
    import json

    parsed_buttons = None
    if buttons.strip():
        try:
            parsed_buttons = json.loads(buttons)
        except Exception:
            parsed_buttons = None

    async with SessionLocal() as db:
        db.add(Keyword(trigger=trigger.strip(), reply=reply.strip(), buttons=parsed_buttons))
        await db.commit()

    return RedirectResponse("/admin", status_code=302)


@router.post("/banned")
async def create_banned(
    request: Request,
    word: str = Form(...)
):
    require_admin_session(request)
    async with SessionLocal() as db:
        db.add(BannedWord(word=word.strip()))
        await db.commit()

    return RedirectResponse("/admin", status_code=302)


@router.post("/welcome")
async def create_welcome(
    request: Request,
    chat_id: int = Form(...),
    text: str = Form(...),
    buttons: str = Form(""),
    enabled: str = Form("true"),
):
    require_admin_session(request)
    import json
    from app.services.welcome_service import set_welcome

    parsed_buttons = None
    if buttons.strip():
        try:
            parsed_buttons = json.loads(buttons)
        except Exception:
            parsed_buttons = None

    await set_welcome(
        chat_id=chat_id,
        text=text.strip(),
        enabled=enabled.lower() == "true",
        buttons=parsed_buttons,
    )
    return RedirectResponse("/admin", status_code=302)


@router.post("/groups/{chat_id}/ai")
async def toggle_group_ai(
    request: Request,
    chat_id: int,
    enabled: bool = Form(...)
):
    require_admin_session(request)
    await set_group_ai(chat_id, enabled)
    return RedirectResponse("/admin", status_code=302)


@router.get("/api/stats")
async def admin_stats(request: Request):
    require_admin_session(request)

    async with SessionLocal() as db:
        data = {
            "admins": await db.scalar(select(func.count(AdminUser.id))),
            "groups": await db.scalar(select(func.count(BotGroup.id))),
            "keywords": await db.scalar(select(func.count(Keyword.id))),
            "banned_words": await db.scalar(select(func.count(BannedWord.id))),
            "welcomes": await db.scalar(select(func.count(WelcomeSetting.id))),
        }

    return JSONResponse(data)
```

---

# 18. `app/main.py`

```python
import traceback

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import text

from aiogram import types

from app.bot.instance import bot, dp
from app.bot.handlers import router as bot_router
from app.core.cache import cache
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine
from app.web.admin import router as admin_router

dp.include_router(bot_router)

app = FastAPI(title="Telegram Bot Modular App")
app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET)
app.include_router(admin_router)


@app.get("/")
async def home():
    return {"ok": True, "service": "telegram-bot"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


async def wait_for_db():
    for i in range(10):
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            print("[DB] connected")
            return
        except Exception as e:
            print(f"[DB] retry {i+1}/10:", repr(e))
    raise RuntimeError("Database unavailable")


@app.on_event("startup")
async def on_startup():
    await wait_for_db()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await cache.connect()

    result = await bot.set_webhook(
        url=settings.webhook_url,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "my_chat_member"],
        secret_token=settings.TELEGRAM_WEBHOOK_SECRET or None,
    )
    print("[WEBHOOK] set:", result, settings.webhook_url)


@app.on_event("shutdown")
async def on_shutdown():
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        pass

    await bot.session.close()
    await engine.dispose()


@app.post("/webhook")
async def telegram_webhook(request: Request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if settings.TELEGRAM_WEBHOOK_SECRET and secret != settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        data = await request.json()
        update = types.Update.model_validate(data)
        await dp.feed_update(bot, update)
        return JSONResponse({"ok": True})
    except Exception as e:
        print("[WEBHOOK ERROR]", repr(e))
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)})
```

---

# 19. `run.py`

```python
import uvicorn
from app.core.config import settings

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, reload=True)
```

---

# 20. Cách chạy

## Cài package

```bash
pip install -r requirements.txt
```

## Chạy local

```bash
python run.py
```

hoặc:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

# 21. Chức năng đang có trong bản này

Bản này đã có:

### Bot
- `/start`
- Theo dõi bot vào group
- Lưu group vào DB
- Welcome member mới
- Tự động reply theo keyword
- Xoá message nếu chứa banned word
- OpenAI chat trong private
- OpenAI chat trong group nếu `ai_enabled=True`

### Web admin
- Login bằng session
- Dashboard HTML đơn giản
- Thêm keyword
- Thêm banned words
- Cấu hình welcome theo group
- Bật/tắt AI cho group
- API stats

### Hệ thống
- Webhook Telegram có secret
- SQLAlchemy async
- Redis fallback RAM
- Chia module sạch
- Có normalize `DATABASE_URL`

---

# 22. Điểm mạnh hơn bản cũ

So với bản cũ của bạn, bản này đã sửa chuẩn các điểm:

- Không còn lỗi `DATABASE_URL is None` rồi `.startswith()`
- `sslmode=require` được xử lý đúng
- Không dùng `?key=...` cho admin nữa
- Có session cookie
- Webhook có verify secret token
- Tách module rõ:
  - `core`
  - `db`
  - `services`
  - `bot`
  - `web`
- OpenAI đưa vào thành service riêng
- Có `run.py` chạy chuẩn

---

# 23. Gợi ý nâng cấp tiếp theo nếu bạn muốn “xịn hơn nữa”

Nếu bạn muốn bản production chuẩn hơn nữa, bước tiếp theo nên thêm:

## A. Alembic migration
Hiện tại đang dùng:

```python
Base.metadata.create_all()
```

Tốt cho demo/MVP, nhưng production nên dùng **Alembic**.

---

## B. Scheduler auto post
Nếu bạn muốn giống bot quản trị đầy đủ, có thể thêm:

- `APScheduler`
- bảng `AutoPost`
- job gửi message định kỳ vào group

---

## C. Admin role phân quyền
Hiện tại web admin login bằng 1 key global.  
Có thể nâng lên:

- user/password
- hashed password
- phân quyền admin/editor/viewer

---

## D. FSM + Redis storage cho aiogram
Hiện tại bot dùng `MemoryStorage`.  
Nếu bạn muốn scale nhiều instance, đổi sang:

- `RedisStorage`

---

## E. Log chuẩn
Thêm:
- `logging`
- request id
- structured logs

---

# 24. Nếu bạn muốn, mình có thể làm tiếp cho bạn 1 trong 3 hướng sau

## Hướng 1 — Bản FULL PRO hơn
Mình viết tiếp cho bạn:
- `AutoPost`
- scheduler
- CRUD xoá/sửa keyword/banned/welcome
- OpenAI agent commands
- cache keyword/banned/admin vào Redis

## Hướng 2 — Bản có template đẹp
Mình thêm:
- `templates/`
- giao diện admin đẹp bằng Jinja2 + Bootstrap
- form sửa/xoá trực tiếp

## Hướng 3 — Bản chuẩn production
Mình nâng cấp:
- Alembic
- Dockerfile
- docker-compose
- nginx reverse proxy
- PostgreSQL + Redis stack

---

Nếu bạn muốn, ở tin nhắn tiếp theo mình có thể gửi luôn:

### **“BẢN FULL PRO V2”**
gồm thêm:
- `AutoPost`
- `APScheduler`
- CRUD đầy đủ
- cache reload
- admin users trong DB
- OpenAI command `/ai on`, `/ai off`
- template admin đẹp hơn

Chỉ cần trả lời:

**“Gửi V2 full pro”**
