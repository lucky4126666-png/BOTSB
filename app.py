import os
import asyncio
import traceback
import html
from datetime import datetime
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command

from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Text,
    DateTime,
    Boolean,
    select,
    text,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base

# =========================
# Optional Redis
# =========================
try:
    import redis.asyncio as redis
except Exception:
    redis = None


# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", "8000"))

TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
ADMIN_WEB_KEY = os.getenv("ADMIN_WEB_KEY", "")
REDIS_URL = os.getenv("REDIS_URL", "")

if not BOT_TOKEN or not BASE_URL or not DATABASE_URL:
    raise RuntimeError("Thiếu BOT_TOKEN / BASE_URL / DATABASE_URL")

BASE_URL = BASE_URL.rstrip("/")


# =========================
# DB URL normalize
# =========================
def normalize_database_url(url: str):
    if not url:
        raise RuntimeError("DATABASE_URL is empty")

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("sqlite:///") and "+aiosqlite" not in url:
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    connect_args = {}

    if parsed.scheme.startswith("postgresql+asyncpg"):
        sslmode = query.pop("sslmode", None)
        connect_timeout = query.pop("connect_timeout", None)

        if sslmode == "require":
            connect_args["ssl"] = True

        if connect_timeout:
            try:
                connect_args["timeout"] = int(connect_timeout)
            except ValueError:
                connect_args["timeout"] = 60
        else:
            connect_args["timeout"] = 60

    elif parsed.scheme.startswith("sqlite+aiosqlite"):
        connect_args["timeout"] = 60

    new_query = urlencode(query)
    new_parsed = parsed._replace(query=new_query)
    new_url = urlunparse(new_parsed)
    return new_url, connect_args


DATABASE_URL, DB_CONNECT_ARGS = normalize_database_url(DATABASE_URL)


# =========================
# Database
# =========================
Base = declarative_base()

if DATABASE_URL.startswith("sqlite+aiosqlite://"):
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        connect_args=DB_CONNECT_ARGS,
    )
else:
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


# =========================
# Models
# =========================
class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    note = Column(String(255), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class BotGroup(Base):
    __tablename__ = "bot_groups"

    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, unique=True, nullable=False, index=True)
    title = Column(String(255), default="")
    username = Column(String(255), default="")
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Keyword(Base):
    __tablename__ = "keywords"

    id = Column(Integer, primary_key=True)
    trigger = Column(String(255), nullable=False, index=True)
    reply_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class BannedWord(Base):
    __tablename__ = "banned_words"

    id = Column(Integer, primary_key=True)
    word = Column(String(255), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WelcomeSetting(Base):
    __tablename__ = "welcome_settings"

    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    enabled = Column(Boolean, default=True)
    message_text = Column(Text, default="Welcome!")
    created_at = Column(DateTime, default=datetime.utcnow)


class AutoPost(Base):
    __tablename__ = "auto_posts"

    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    text = Column(Text, nullable=False)
    interval_minutes = Column(Integer, default=60)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# =========================
# Cache
# =========================
admin_cache = set()
keyword_cache = []
banned_cache = []


# =========================
# KV storage: Redis or RAM fallback
# =========================
class MemoryKV:
    def __init__(self):
        self.store = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value, ex: int | None = None):
        self.store[key] = value

    async def delete(self, key: str):
        self.store.pop(key, None)


kv = MemoryKV()


async def init_kv():
    global kv
    if redis and REDIS_URL:
        try:
            r = redis.from_url(REDIS_URL, decode_responses=True)
            await r.ping()
            kv = r
            print("[KV] Redis connected")
            return
        except Exception as e:
            print("[KV] Redis failed, fallback RAM:", repr(e))

    kv = MemoryKV()
    print("[KV] Using in-memory fallback")


# =========================
# Bot / Dispatcher / App
# =========================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()
app = FastAPI(title="Telegram Bot Admin")


# =========================
# Helpers
# =========================
async def wait_for_db(max_retries: int = 20, delay: int = 3):
    for i in range(max_retries):
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            print("[DB] Connected")
            return
        except Exception as e:
            print(f"[DB] Retry {i+1}/{max_retries} failed:", repr(e))
            await asyncio.sleep(delay)
    raise RuntimeError("Database connection failed after retries")


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[DB] Tables ready")


async def load_admin_cache():
    global admin_cache
    async with SessionLocal() as db:
        rows = (await db.execute(select(AdminUser.user_id))).scalars().all()
        admin_cache = set(rows)
    print(f"[CACHE] admin_cache={len(admin_cache)}")


async def reload_keyword_cache():
    global keyword_cache
    async with SessionLocal() as db:
        keyword_cache = (
            await db.execute(select(Keyword).order_by(Keyword.id.desc()))
        ).scalars().all()
    print(f"[CACHE] keyword_cache={len(keyword_cache)}")


async def reload_banned_cache():
    global banned_cache
    async with SessionLocal() as db:
        banned_cache = (
            await db.execute(select(BannedWord).order_by(BannedWord.id.asc()))
        ).scalars().all()
    print(f"[CACHE] banned_cache={len(banned_cache)}")


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
        admins = (
            await db.execute(select(AdminUser).order_by(AdminUser.id.desc()))
        ).scalars().all()

        groups = (
            await db.execute(select(BotGroup).order_by(BotGroup.updated_at.desc()))
        ).scalars().all()

        keywords = (
            await db.execute(select(Keyword).order_by(Keyword.id.desc()))
        ).scalars().all()

        welcomes = (
            await db.execute(select(WelcomeSetting).order_by(WelcomeSetting.id.desc()))
        ).scalars().all()

        autos = (
            await db.execute(select(AutoPost).order_by(AutoPost.id.desc()))
        ).scalars().all()

    return admins, groups, keywords, welcomes, autos


def is_admin_user(user_id: int) -> bool:
    return user_id in admin_cache


def find_banned_word(text_value: str):
    t = text_value.lower()
    for item in banned_cache:
        if item.word.lower() in t:
            return item.word
    return None


def find_keyword(text_value: str):
    t = text_value.lower()
    for item in keyword_cache:
        if item.trigger.lower() in t:
            return item
    return None


async def record_group(chat: types.Chat, is_admin: bool | None = None, is_active: bool = True):
    if chat.type not in ("group", "supergroup"):
        return

    async with SessionLocal() as db:
        row = (
            await db.execute(select(BotGroup).where(BotGroup.chat_id == chat.id))
        ).scalar_one_or_none()

        if row is None:
            row = BotGroup(
                chat_id=chat.id,
                title=chat.title or "",
                username=chat.username or "",
                is_active=is_active,
                is_admin=bool(is_admin) if is_admin is not None else False,
            )
            db.add(row)
        else:
            row.title = chat.title or row.title or ""
            row.username = chat.username or ""
            row.is_active = is_active
            if is_admin is not None:
                row.is_admin = bool(is_admin)
            row.updated_at = datetime.utcnow()

        await db.commit()


def require_admin_cookie(request: Request):
    if not ADMIN_WEB_KEY:
        raise HTTPException(status_code=503, detail="ADMIN_WEB_KEY chưa được cấu hình")

    cookie_key = request.cookies.get("admin_key", "")
    if cookie_key != ADMIN_WEB_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def html_page(title: str, body: str):
    return HTMLResponse(
        f"""
        <html>
        <head>
            <meta charset="utf-8" />
            <title>{html.escape(title)}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 30px; }}
                input, textarea, button {{ margin: 6px 0; padding: 8px; width: 100%; max-width: 600px; }}
                table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background: #f5f5f5; }}
                .box {{ border: 1px solid #ddd; padding: 16px; margin-bottom: 20px; border-radius: 8px; }}
                .row {{ display: flex; gap: 24px; flex-wrap: wrap; }}
                .col {{ flex: 1; min-width: 320px; }}
                a {{ text-decoration: none; }}
            </style>
        </head>
        <body>
            {body}
        </body>
        </html>
        """
    )


# =========================
# Aiogram handlers
# =========================
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if message.chat.type == "private":
        await message.answer("Bot đang hoạt động bình thường.")


@dp.message(Command("id"))
async def id_cmd(message: types.Message):
    await message.reply(
        f"chat_id={message.chat.id}\nuser_id={message.from_user.id if message.from_user else 'unknown'}"
    )


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def group_message_handler(message: types.Message):
    await record_group(message.chat)

    if not message.text:
        return

    bad = find_banned_word(message.text)
    if bad:
        await message.reply(f"Phát hiện từ bị cấm: {bad}")
        return

    kw = find_keyword(message.text)
    if kw:
        await message.reply(kw.reply_text)


@dp.my_chat_member()
async def track_bot_membership(event: types.ChatMemberUpdated):
    if event.chat.type not in ("group", "supergroup"):
        return

    status = event.new_chat_member.status
    is_active = status not in ("left", "kicked")
    is_admin = status in ("administrator", "creator")

    await record_group(event.chat, is_admin=is_admin, is_active=is_active)


# =========================
# FastAPI routes
# =========================
@app.get("/", response_class=HTMLResponse)
async def index():
    return html_page(
        "Bot Service",
        """
        <h2>Bot Service OK</h2>
        <p><a href="/health">/health</a></p>
        <p><a href="/admin">/admin</a></p>
        """,
    )


@app.get("/health")
async def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}


@app.post("/webhook")
async def webhook(request: Request):
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


# =========================
# Web Admin
# =========================
@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page():
    return html_page(
        "Admin Login",
        """
        <h2>Admin Login</h2>
        <form method="post" action="/admin/login">
            <label>Admin Key</label>
            <input type="password" name="key" placeholder="Nhập ADMIN_WEB_KEY" />
            <button type="submit">Đăng nhập</button>
        </form>
        """,
    )


@app.post("/admin/login")
async def admin_login(key: str = Form(...)):
    if not ADMIN_WEB_KEY:
        raise HTTPException(status_code=503, detail="ADMIN_WEB_KEY chưa được cấu hình")

    if key != ADMIN_WEB_KEY:
        return html_page("Login failed", "<h3>Sai key</h3><p><a href='/admin/login'>Thử lại</a></p>")

    resp = RedirectResponse("/admin", status_code=302)
    resp.set_cookie(
        "admin_key",
        value=key,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=86400,
    )
    return resp


@app.get("/admin/logout")
async def admin_logout():
    resp = RedirectResponse("/admin/login", status_code=302)
    resp.delete_cookie("admin_key")
    return resp


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    require_admin_cookie(request)

    admins, groups, keywords, welcomes, autos = await fetch_web_data()

    group_rows = "".join(
        f"""
        <tr>
            <td>{g.chat_id}</td>
            <td>{html.escape(g.title or '')}</td>
            <td>{html.escape(g.username or '')}</td>
            <td>{'Yes' if g.is_active else 'No'}</td>
            <td>{'Yes' if g.is_admin else 'No'}</td>
            <td>{g.updated_at}</td>
        </tr>
        """
        for g in groups
    ) or "<tr><td colspan='6'>No groups</td></tr>"

    admin_rows = "".join(
        f"<li>{a.user_id} - {html.escape(a.note or '')}</li>"
        for a in admins
    ) or "<li>No admins</li>"

    keyword_rows = "".join(
        f"<li><b>{html.escape(k.trigger)}</b> → {html.escape(k.reply_text)}</li>"
        for k in keywords
    ) or "<li>No keywords</li>"

    banned_rows = "".join(
        f"<li>{html.escape(b.word)}</li>"
        for b in banned_cache
    ) or "<li>No banned words</li>"

    body = f"""
    <h2>Admin Dashboard</h2>
    <p>
        <a href="/admin/logout">Logout</a>
    </p>

    <div class="row">
        <div class="col box">
            <h3>Stats</h3>
            <ul>
                <li>Admins: {len(admins)}</li>
                <li>Groups: {len(groups)}</li>
                <li>Keywords: {len(keywords)}</li>
                <li>Welcome Settings: {len(welcomes)}</li>
                <li>Auto Posts: {len(autos)}</li>
                <li>Banned Words: {len(banned_cache)}</li>
            </ul>

            <form method="post" action="/admin/reload-caches">
                <button type="submit">Reload caches</button>
            </form>
        </div>

        <div class="col box">
            <h3>Add Admin User</h3>
            <form method="post" action="/admin/admins/add">
                <input type="number" name="user_id" placeholder="Telegram user_id" />
                <input type="text" name="note" placeholder="Note" />
                <button type="submit">Add Admin</button>
            </form>
        </div>

        <div class="col box">
            <h3>Add Keyword</h3>
            <form method="post" action="/admin/keywords/add">
                <input type="text" name="trigger" placeholder="Trigger" />
                <textarea name="reply_text" placeholder="Reply text"></textarea>
                <button type="submit">Add Keyword</button>
            </form>
        </div>

        <div class="col box">
            <h3>Add Banned Word</h3>
            <form method="post" action="/admin/banned/add">
                <input type="text" name="word" placeholder="Banned word" />
                <button type="submit">Add Banned Word</button>
            </form>
        </div>
    </div>

    <div class="box">
        <h3>Admin Users</h3>
        <ul>{admin_rows}</ul>
    </div>

    <div class="box">
        <h3>Keywords</h3>
        <ul>{keyword_rows}</ul>
    </div>

    <div class="box">
        <h3>Banned Words</h3>
        <ul>{banned_rows}</ul>
    </div>

    <div class="box">
        <h3>Groups</h3>
        <table>
            <thead>
                <tr>
                    <th>chat_id</th>
                    <th>title</th>
                    <th>username</th>
                    <th>active</th>
                    <th>bot admin</th>
                    <th>updated_at</th>
                </tr>
            </thead>
            <tbody>
                {group_rows}
            </tbody>
        </table>
    </div>
    """

    return html_page("Admin Dashboard", body)


@app.post("/admin/reload-caches")
async def admin_reload_caches(request: Request):
    require_admin_cookie(request)
    await load_admin_cache()
    await reload_keyword_cache()
    await reload_banned_cache()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/admins/add")
async def admin_add_admin(
    request: Request,
    user_id: int = Form(...),
    note: str = Form(""),
):
    require_admin_cookie(request)

    async with SessionLocal() as db:
        exists = (
            await db.execute(select(AdminUser).where(AdminUser.user_id == user_id))
        ).scalar_one_or_none()

        if not exists:
            db.add(AdminUser(user_id=user_id, note=note))
            await db.commit()

    await load_admin_cache()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/keywords/add")
async def admin_add_keyword(
    request: Request,
    trigger: str = Form(...),
    reply_text: str = Form(...),
):
    require_admin_cookie(request)

    trigger = trigger.strip()
    reply_text = reply_text.strip()

    if not trigger or not reply_text:
        raise HTTPException(status_code=400, detail="trigger/reply_text is required")

    async with SessionLocal() as db:
        db.add(Keyword(trigger=trigger, reply_text=reply_text))
        await db.commit()

    await reload_keyword_cache()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/banned/add")
async def admin_add_banned_word(
    request: Request,
    word: str = Form(...),
):
    require_admin_cookie(request)

    word = word.strip()
    if not word:
        raise HTTPException(status_code=400, detail="word is required")

    async with SessionLocal() as db:
        exists = (
            await db.execute(select(BannedWord).where(BannedWord.word == word))
        ).scalar_one_or_none()

        if not exists:
            db.add(BannedWord(word=word))
            await db.commit()

    await reload_banned_cache()
    return RedirectResponse("/admin", status_code=302)


# =========================
# Startup / Shutdown
# =========================
@app.on_event("startup")
async def on_startup():
    print("[APP] startup")
    await wait_for_db()
    await init_db()
    await init_kv()

    await load_admin_cache()
    await reload_keyword_cache()
    await reload_banned_cache()

    webhook_url = f"{BASE_URL}/webhook"
    result = await bot.set_webhook(
        webhook_url,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "my_chat_member"],
        secret_token=TELEGRAM_WEBHOOK_SECRET or None,
    )
    print("[WEBHOOK] set:", result, webhook_url)


@app.on_event("shutdown")
async def on_shutdown():
    print("[APP] shutdown")
    try:
        await bot.session.close()
    except Exception:
        pass
    try:
        await engine.dispose()
    except Exception:
        pass


# =========================
# Main
# =========================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
