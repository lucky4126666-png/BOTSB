import os
import json
import html
import asyncio
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command, CommandStart
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from sqlalchemy import (
    String,
    Text,
    BigInteger,
    Integer,
    Boolean,
    DateTime,
    select,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)

# -------------------------
# Optional imports
# -------------------------
try:
    import redis.asyncio as redis
except Exception:
    redis = None

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

# -------------------------
# ENV
# -------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BASE_URL = os.getenv("BASE_URL", "").strip().rstrip("/")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PORT = int(os.getenv("PORT", "8000"))

TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
ADMIN_LOGIN_KEY = os.getenv("ADMIN_LOGIN_KEY", "").strip()
ADMIN_SESSION_SECRET = os.getenv("ADMIN_SESSION_SECRET", "change_me_please").strip()

REDIS_URL = os.getenv("REDIS_URL", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

if not BOT_TOKEN or not BASE_URL or not DATABASE_URL:
    raise RuntimeError("Thiếu BOT_TOKEN / BASE_URL / DATABASE_URL")


# -------------------------
# Helpers
# -------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_database_url(url: str):
    connect_args = {}

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("sqlite:///"):
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    elif url.startswith("sqlite://") and "+aiosqlite" not in url:
        url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    # asyncpg dùng ssl=require, không phải sslmode=require
    url = url.replace("sslmode=require", "ssl=require")

    return url, connect_args


def parse_buttons_text(raw: str):
    """
    Format:
    Text 1 | https://example.com
    Text 2 | tg://resolve?domain=abc
    """
    items = []
    raw = (raw or "").strip()
    if not raw:
        return []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            continue
        text, url = [x.strip() for x in line.split("|", 1)]
        if not text or not url:
            continue
        if not (
            url.startswith("http://")
            or url.startswith("https://")
            or url.startswith("tg://")
        ):
            continue
        items.append({"text": text[:64], "url": url})
    return items


def build_inline_keyboard(buttons_json: Optional[str]):
    if not buttons_json:
        return None
    try:
        data = json.loads(buttons_json)
        rows = []
        for item in data:
            if not item.get("text") or not item.get("url"):
                continue
            rows.append(
                [
                    types.InlineKeyboardButton(
                        text=item["text"],
                        url=item["url"],
                    )
                ]
            )
        if not rows:
            return None
        return types.InlineKeyboardMarkup(inline_keyboard=rows)
    except Exception:
        return None

def esc(s: str) -> str:
    return html.escape(str(s or ""))


# -------------------------
# DB
# -------------------------
DATABASE_URL, DB_CONNECT_ARGS = normalize_database_url(DATABASE_URL)

class Base(DeclarativeBase):
    pass

class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BotGroup(Base):
    __tablename__ = "bot_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    username: Mapped[str] = mapped_column(String(255), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger: Mapped[str] = mapped_column(String(255), index=True)
    response: Mapped[str] = mapped_column(Text)
    buttons_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BannedWord(Base):
    __tablename__ = "banned_words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WelcomeSetting(Base):
    __tablename__ = "welcome_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    message: Mapped[str] = mapped_column(Text, default="Xin chào {user}")
    buttons_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AutoPost(Base):
    __tablename__ = "auto_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message: Mapped[str] = mapped_column(Text)
    buttons_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


engine_kwargs = {
    "echo": False,
    "pool_pre_ping": True,
    "connect_args": DB_CONNECT_ARGS,
}
if not DATABASE_URL.startswith("sqlite+aiosqlite"):
    engine_kwargs["pool_recycle"] = 300
    engine_kwargs["pool_timeout"] = 60

engine = create_async_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# -------------------------
# Bot / App
# -------------------------
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
router = Router()
dp.include_router(router)

app = FastAPI(title="Telegram Bot Full Stable")
app.add_middleware(
    SessionMiddleware,
    secret_key=ADMIN_SESSION_SECRET,
    same_site="lax",
    https_only=False,
)

# -------------------------
# Optional Redis / OpenAI
# -------------------------
redis_client = None
openai_client = None

# -------------------------
# In-memory cache
# -------------------------
admin_cache: set[int] = set()
keyword_cache: list[Keyword] = []
banned_cache: list[str] = []
welcome_cache: dict[int, WelcomeSetting] = {}

autopost_stop = asyncio.Event()
autopost_task = None

# -------------------------
# DB utilities
# -------------------------
async def wait_for_db(max_retry: int = 15, delay: int = 2):
    for i in range(max_retry):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return
        except Exception as e:
            print(f"[DB] wait retry {i+1}/{max_retry}: {e}")
            await asyncio.sleep(delay)
    raise RuntimeError("Không kết nối được database")


async def load_admin_cache():
    global admin_cache
    async with SessionLocal() as db:
        rows = (await db.execute(select(AdminUser.user_id))).scalars().all()
        admin_cache = set(rows)


async def reload_keyword_cache():
    global keyword_cache
    async with SessionLocal() as db:
        keyword_cache = (
            await db.execute(select(Keyword).where(Keyword.enabled == True).order_by(Keyword.id.desc()))
        ).scalars().all()


async def reload_banned_cache():
    global banned_cache
    async with SessionLocal() as db:
        rows = (
            await db.execute(select(BannedWord.word).order_by(BannedWord.id.asc()))
        ).scalars().all()
        banned_cache = [x.lower().strip() for x in rows if x and x.strip()]


async def reload_welcome_cache():
    global welcome_cache
    async with SessionLocal() as db:
        rows = (
            await db.execute(select(WelcomeSetting).where(WelcomeSetting.enabled == True))
        ).scalars().all()
        welcome_cache = {x.chat_id: x for x in rows}


async def load_all_cache():
    await load_admin_cache()
    await reload_keyword_cache()
    await reload_banned_cache()
    await reload_welcome_cache()


async def get_all_groups():
    async with SessionLocal() as db:
        return (
            await db.execute(select(BotGroup).order_by(BotGroup.updated_at.desc()))
        ).scalars().all()


async def get_admin_groups():
    async with SessionLocal() as db:
        return (
            await db.execute(
                select(BotGroup)
                .where(BotGroup.is_admin == True)
                .order_by(BotGroup.updated_at.desc())
            )
        ).scalars().all()


async def fetch_web_data():
    async with SessionLocal() as db:
        admins = (await db.execute(select(AdminUser).order_by(AdminUser.id.desc()))).scalars().all()
        groups = (await db.execute(select(BotGroup).order_by(BotGroup.updated_at.desc()))).scalars().all()
        keywords = (await db.execute(select(Keyword).order_by(Keyword.id.desc()))).scalars().all()
        welcomes = (await db.execute(select(WelcomeSetting).order_by(WelcomeSetting.id.desc()))).scalars().all()
        autos = (await db.execute(select(AutoPost).order_by(AutoPost.id.desc()))).scalars().all()
        banned = (await db.execute(select(BannedWord).order_by(BannedWord.id.desc()))).scalars().all()
    return admins, groups, keywords, welcomes, autos, banned


async def upsert_group(chat_id: int, title: str, username: str = "", is_admin: bool = False):
    async with SessionLocal() as db:
        row = (
            await db.execute(select(BotGroup).where(BotGroup.chat_id == chat_id))
        ).scalar_one_or_none()

        if row:
            row.title = title or ""
            row.username = username or ""
            row.is_admin = is_admin
            row.updated_at = utcnow()
        else:
            row = BotGroup(
                chat_id=chat_id,
                title=title or "",
                username=username or "",
                is_admin=is_admin,
                updated_at=utcnow(),
            )
            db.add(row)
        await db.commit()


# -------------------------
# Redis / OpenAI
# -------------------------
async def init_redis():
    global redis_client
    if not REDIS_URL or redis is None:
        print("[REDIS] disabled")
        return
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping()
        print("[REDIS] connected")
    except Exception as e:
        redis_client = None
        print("[REDIS] fallback RAM:", e)


def init_openai():
    global openai_client
    if OPENAI_API_KEY and AsyncOpenAI is not None:
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        print("[OPENAI] enabled")
    else:
        openai_client = None
        print("[OPENAI] disabled")


async def ask_ai(prompt: str) -> str:
    if not openai_client:
        return "AI chưa được cấu hình."
    try:
        resp = await openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Bạn là trợ lý ngắn gọn, hữu ích."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
        )
        return resp.choices[0].message.content or "Không có phản hồi."
    except Exception as e:
        return f"Lỗi AI: {e}"


# -------------------------
# Bot helpers
# -------------------------
def is_tg_admin(user_id: int) -> bool:
    return user_id in admin_cache


# -------------------------
# Bot handlers
# -------------------------
@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("Bot đang hoạt động ổn định ✅")


@router.message(Command("reload"))
async def cmd_reload(message: types.Message):
    if not message.from_user or not is_tg_admin(message.from_user.id):
        return await message.answer("Bạn không có quyền.")
    await load_all_cache()
    await message.answer("Đã reload cache ✅")


@router.message(Command("ai"))
async def cmd_ai(message: types.Message):
    if not message.from_user or not is_tg_admin(message.from_user.id):
        return await message.answer("Bạn không có quyền dùng AI.")
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("Dùng: /ai nội dung")
    answer = await ask_ai(parts[1])
    await message.answer(answer[:4000])


@router.my_chat_member()
async def track_bot_membership(event: types.ChatMemberUpdated):
    try:
        chat = event.chat
        status = event.new_chat_member.status
        active = status not in ("left", "kicked")

        if chat.type in ("group", "supergroup"):
            await upsert_group(
                chat_id=chat.id,
                title=chat.title or "",
                username=getattr(chat, "username", "") or "",
                is_admin=active,
            )
    except Exception:
        traceback.print_exc()


@router.message(F.new_chat_members)
async def welcome_new_members(message: types.Message):
    setting = welcome_cache.get(message.chat.id)
    if not setting or not setting.enabled:
        return

    for member in message.new_chat_members:
        try:
            text = (
                setting.message
                .replace("{user}", esc(member.full_name))
                .replace("{chat}", esc(message.chat.title or "group"))
            )
            await message.answer(
                text,
                reply_markup=build_inline_keyboard(setting.buttons_json),
            )
        except Exception:
            traceback.print_exc()


@router.message(F.text)
async def handle_text(message: types.Message):
    if not message.text:
        return

    text = message.text.strip()
    lower = text.lower()

    # update group seen
    if message.chat.type in ("group", "supergroup"):
        await upsert_group(
            chat_id=message.chat.id,
            title=message.chat.title or "",
            username=getattr(message.chat, "username", "") or "",
            is_admin=True,
        )

    # banned words
    for word in banned_cache:
        if word and word in lower:
            if message.chat.type in ("group", "supergroup"):
                try:
                    await message.delete()
                    return
                except Exception:
                    return
            return

    # keyword auto reply
    for kw in keyword_cache:
        if kw.enabled and kw.trigger.lower() in lower:
            try:
                await message.answer(
                    kw.response[:4000],
                    reply_markup=build_inline_keyboard(kw.buttons_json),
                )
            except Exception:
                traceback.print_exc()
            return
# -------------------------
# Auto post worker
# -------------------------
async def autopost_worker():
    print("[AUTOPOST] started")
    while not autopost_stop.is_set():
        try:
            now = utcnow()
            async with SessionLocal() as db:
                rows = (
                    await db.execute(
                        select(AutoPost).where(
                            AutoPost.enabled == True,
                            AutoPost.next_run_at <= now,
                        )
                    )
                ).scalars().all()

                for item in rows:
                    try:
                        await bot.send_message(
                            item.chat_id,
                            item.message[:4000],
                            reply_markup=build_inline_keyboard(item.buttons_json),
                        )
                        item.next_run_at = now + timedelta(minutes=max(1, item.interval_minutes))
                    except Exception as e:
                        print(f"[AUTOPOST ERROR] id={item.id}: {e}")
                        item.next_run_at = now + timedelta(minutes=max(5, item.interval_minutes))

                await db.commit()
        except Exception:
            traceback.print_exc()

        try:
            await asyncio.wait_for(autopost_stop.wait(), timeout=20)
        except asyncio.TimeoutError:
            pass

    print("[AUTOPOST] stopped")


# -------------------------
# Admin auth
# -------------------------
async def require_admin_web(request: Request):
    if not request.session.get("admin_ok"):
        raise HTTPException(status_code=401, detail="Unauthorized")


def admin_layout(body: str) -> str:
    return f"""
    <html>
    <head>
        <meta charset="utf-8"/>
        <title>Admin Panel</title>
        <style>
            body {{ font-family: Arial; max-width: 1100px; margin: 20px auto; padding: 0 15px; }}
            textarea,input {{ width: 100%; padding: 8px; margin: 6px 0; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
            th,td {{ border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }}
            .card {{ border: 1px solid #ddd; padding: 15px; margin-bottom: 20px; border-radius: 8px; }}
            .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
            a.button, button {{ padding: 8px 12px; display: inline-block; }}
        </style>
    </head>
    <body>
        <p>
            <a href="/admin">Dashboard</a> |
            <a href="/admin/logout">Logout</a>
        </p>
        {body}
    </body>
    </html>
    """


# -------------------------
# Web routes
# -------------------------
@app.get("/", response_class=HTMLResponse)
async def home():
    return "<h3>Bot server running ✅</h3>"


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_get():
    return """
    <html><body>
    <h2>Admin Login</h2>
    <form method="post" action="/admin/login">
        <input type="password" name="key" placeholder="Admin key"/>
        <button type="submit">Login</button>
    </form>
    </body></html>
    """


@app.post("/admin/login")
async def admin_login_post(request: Request, key: str = Form(...)):
    if not ADMIN_LOGIN_KEY:
        raise HTTPException(status_code=500, detail="ADMIN_LOGIN_KEY chưa cấu hình")
    if key != ADMIN_LOGIN_KEY:
        return HTMLResponse("<h3>Sai key</h3>", status_code=401)
    request.session["admin_ok"] = True
    return RedirectResponse("/admin", status_code=302)


@app.get("/admin/logout")
async def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=302)


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not request.session.get("admin_ok"):
        return RedirectResponse("/admin/login", status_code=302)

    admins, groups, keywords, welcomes, autos, banned = await fetch_web_data()

    body = f"""
    <h1>Admin Dashboard</h1>

    <div class="card">
        <h3>Stats</h3>
        <ul>
            <li>Admins TG: {len(admins)}</li>
            <li>Groups: {len(groups)}</li>
            <li>Keywords: {len(keywords)}</li>
            <li>Welcome settings: {len(welcomes)}</li>
            <li>Auto posts: {len(autos)}</li>
            <li>Banned words: {len(banned)}</li>
        </ul>
    </div>

    <div class="row">
        <div class="card">
            <h3>Thêm Telegram Admin</h3>
            <form method="post" action="/admin/add-admin">
                <input name="user_id" placeholder="Telegram user id" />
                <button type="submit">Add</button>
            </form>
        </div>

        <div class="card">
            <h3>Thêm Banned Word</h3>
            <form method="post" action="/admin/add-banned">
                <input name="word" placeholder="word" />
                <button type="submit">Add</button>
            </form>
        </div>
    </div>

    <div class="card">
        <h3>Thêm Keyword</h3>
        <form method="post" action="/admin/add-keyword">
            <input name="trigger" placeholder="trigger" />
            <textarea name="response" rows="4" placeholder="response"></textarea>
            <textarea name="buttons" rows="4" placeholder="Button text | https://url"></textarea>
            <button type="submit">Add keyword</button>
        </form>
    </div>

    <div class="card">
        <h3>Thêm Welcome Setting</h3>
        <form method="post" action="/admin/add-welcome">
            <input name="chat_id" placeholder="group chat id" />
            <textarea name="message" rows="4" placeholder="Xin chào {user} đến với {chat}"></textarea>
            <textarea name="buttons" rows="4" placeholder="Button text | https://url"></textarea>
            <button type="submit">Save welcome</button>
        </form>
    </div>

    <div class="card">
        <h3>Thêm Auto Post</h3>
        <form method="post" action="/admin/add-autopost">
            <input name="chat_id" placeholder="group chat id" />
            <input name="interval_minutes" placeholder="interval minutes" />
            <textarea name="message" rows="4" placeholder="message"></textarea>
            <textarea name="buttons" rows="4" placeholder="Button text | https://url"></textarea>
            <button type="submit">Add auto post</button>
        </form>
    </div>

    <div class="card">
        <h3>Groups</h3>
        <table>
            <tr><th>ID</th><th>Chat ID</th><th>Title</th><th>Admin</th><th>Updated</th></tr>
            {''.join(
                f"<tr><td>{g.id}</td><td>{g.chat_id}</td><td>{esc(g.title)}</td><td>{g.is_admin}</td><td>{g.updated_at}</td></tr>"
                for g in groups
            )}
        </table>
    </div>

    <div class="card">
        <h3>Keywords</h3>
        <table>
            <tr><th>ID</th><th>Trigger</th><th>Response</th><th>Enabled</th></tr>
            {''.join(
                f"<tr><td>{k.id}</td><td>{esc(k.trigger)}</td><td>{esc(k.response)}</td><td>{k.enabled}</td></tr>"
                for k in keywords
            )}
        </table>
    </div>

    <div class="card">
        <h3>Welcome</h3>
        <table>
            <tr><th>ID</th><th>Chat ID</th><th>Message</th><th>Enabled</th></tr>
            {''.join(
                f"<tr><td>{w.id}</td><td>{w.chat_id}</td><td>{esc(w.message)}</td><td>{w.enabled}</td></tr>"
                for w in welcomes
            )}
        </table>
    </div>

    <div class="card">
        <h3>Auto Posts</h3>
        <table>
            <tr><th>ID</th><th>Chat ID</th><th>Interval</th><th>Next Run</th><th>Enabled</th></tr>
            {''.join(
                f"<tr><td>{a.id}</td><td>{a.chat_id}</td><td>{a.interval_minutes}m</td><td>{a.next_run_at}</td><td>{a.enabled}</td></tr>"
                for a in autos
            )}
        </table>
    </div>

    <div class="card">
        <h3>Banned Words</h3>
        <table>
            <tr><th>ID</th><th>Word</th></tr>
            {''.join(
                f"<tr><td>{b.id}</td><td>{esc(b.word)}</td></tr>"
                for b in banned
            )}
        </table>
    </div>
    """
    return HTMLResponse(admin_layout(body))


@app.post("/admin/add-admin")
async def admin_add_admin(request: Request, user_id: str = Form(...), _: None = Depends(require_admin_web)):
    try:
        uid = int(user_id.strip())
    except Exception:
        return HTMLResponse("user_id không hợp lệ", status_code=400)

    async with SessionLocal() as db:
        exists = (await db.execute(select(AdminUser).where(AdminUser.user_id == uid))).scalar_one_or_none()
        if not exists:
            db.add(AdminUser(user_id=uid))
            await db.commit()

    await load_admin_cache()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/add-banned")
async def admin_add_banned(request: Request, word: str = Form(...), _: None = Depends(require_admin_web)):
    word = word.strip().lower()
    if not word:
        return HTMLResponse("word rỗng", status_code=400)

    async with SessionLocal() as db:
        exists = (await db.execute(select(BannedWord).where(BannedWord.word == word))).scalar_one_or_none()
        if not exists:
            db.add(BannedWord(word=word))
            await db.commit()

    await reload_banned_cache()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/add-keyword")
async def admin_add_keyword(
    request: Request,
    trigger: str = Form(...),
    response: str = Form(...),
    buttons: str = Form(""),
    _: None = Depends(require_admin_web),
):
    trigger = trigger.strip()
    response = response.strip()

    if not trigger or not response:
        return HTMLResponse("trigger/response không được rỗng", status_code=400)

    buttons_json = json.dumps(parse_buttons_text(buttons), ensure_ascii=False) if buttons.strip() else None

    async with SessionLocal() as db:
        db.add(
            Keyword(
                trigger=trigger,
                response=response,
                buttons_json=buttons_json,
                enabled=True,
            )
        )
        await db.commit()

    await reload_keyword_cache()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/add-welcome")
async def admin_add_welcome(
    request: Request,
    chat_id: str = Form(...),
    message: str = Form(...),
    buttons: str = Form(""),
    _: None = Depends(require_admin_web),
):
    try:
        cid = int(chat_id.strip())
    except Exception:
        return HTMLResponse("chat_id không hợp lệ", status_code=400)

    msg = message.strip()
    if not msg:
        return HTMLResponse("message rỗng", status_code=400)

    buttons_json = json.dumps(parse_buttons_text(buttons), ensure_ascii=False) if buttons.strip() else None

    async with SessionLocal() as db:
        row = (
            await db.execute(select(WelcomeSetting).where(WelcomeSetting.chat_id == cid))
        ).scalar_one_or_none()
        if row:
            row.message = msg
            row.buttons_json = buttons_json
            row.enabled = True
            row.updated_at = utcnow()
        else:
            db.add(
                WelcomeSetting(
                    chat_id=cid,
                    message=msg,
                    buttons_json=buttons_json,
                    enabled=True,
                    updated_at=utcnow(),
                )
            )
        await db.commit()

    await reload_welcome_cache()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/add-autopost")
async def admin_add_autopost(
    request: Request,
    chat_id: str = Form(...),
    interval_minutes: str = Form(...),
    message: str = Form(...),
    buttons: str = Form(""),
    _: None = Depends(require_admin_web),
):
    try:
        cid = int(chat_id.strip())
        iv = max(1, int(interval_minutes.strip()))
    except Exception:
        return HTMLResponse("chat_id / interval_minutes không hợp lệ", status_code=400)

    msg = message.strip()
    if not msg:
        return HTMLResponse("message rỗng", status_code=400)

    buttons_json = json.dumps(parse_buttons_text(buttons), ensure_ascii=False) if buttons.strip() else None

    async with SessionLocal() as db:
        db.add(
            AutoPost(
                chat_id=cid,
                message=msg,
                buttons_json=buttons_json,
                interval_minutes=iv,
                enabled=True,
                next_run_at=utcnow() + timedelta(minutes=iv),
            )
        )
        await db.commit()

    return RedirectResponse("/admin", status_code=302)


# -------------------------
# Telegram webhook
# -------------------------
@app.post("/webhook")
async def telegram_webhook(request: Request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if TELEGRAM_WEBHOOK_SECRET and secret != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        data = await request.json()
        update = types.Update.model_validate(data)
        await dp.feed_update(bot, update)
        return {"ok": True}
    except Exception as e:
        print("[WEBHOOK ERROR]", repr(e))
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# -------------------------
# Startup / Shutdown
# -------------------------
@app.on_event("startup")
async def on_startup():
    global autopost_task

    print("[STARTUP] init...")
    await wait_for_db()
    await init_redis()
    init_openai()
    await load_all_cache()

    webhook_url = f"{BASE_URL}/webhook"
    result = await bot.set_webhook(
        webhook_url,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "my_chat_member"],
        secret_token=TELEGRAM_WEBHOOK_SECRET or None,
    )
    print("[WEBHOOK SET]", result, webhook_url)

    autopost_stop.clear()
    autopost_task = asyncio.create_task(autopost_worker())

    print("[STARTUP] done")


@app.on_event("shutdown")
async def on_shutdown():
    print("[SHUTDOWN] stopping...")

    try:
        autopost_stop.set()
        if autopost_task:
            await autopost_task
    except Exception:
        traceback.print_exc()

    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        traceback.print_exc()

    try:
        await bot.session.close()
    except Exception:
        traceback.print_exc()

    try:
        if redis_client:
            await redis_client.close()
    except Exception:
        traceback.print_exc()

    try:
        await engine.dispose()
    except Exception:
        traceback.print_exc()

    print("[SHUTDOWN] done")


# -------------------------
# Local run
# -------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
