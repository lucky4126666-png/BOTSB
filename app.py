import os
import json
import html
import asyncio
import traceback
from datetime import datetime
from typing import Optional, List, Tuple
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from fastapi import FastAPI, Request, HTTPException, Form, status
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from sqlalchemy import (
    String,
    Integer,
    BigInteger,
    Text,
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

from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties

try:
    import redis.asyncio as redis
except Exception:
    redis = None


# =========================
# ENV / CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

REDIS_URL = os.getenv("REDIS_URL", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
ADMIN_WEB_KEY = os.getenv("ADMIN_WEB_KEY", "change-me")
PORT = int(os.getenv("PORT", "8000"))

if not BOT_TOKEN or not BASE_URL or not DATABASE_URL:
    raise RuntimeError("缺少 BOT_TOKEN / BASE_URL / DATABASE_URL")

BASE_URL = BASE_URL.rstrip("/")


def normalize_database_url(db_url: str) -> tuple[str, dict]:
    """
    统一转换数据库 URL，并抽取 connect_args。
    支持:
    - postgres:// -> postgresql+asyncpg://
    - postgresql:// -> postgresql+asyncpg://
    - sqlite:/// -> sqlite+aiosqlite:///
    - 处理 sslmode=require
    """
    connect_args = {}

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("sqlite:///"):
        db_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

    parsed = urlparse(db_url)
    query = dict(parse_qsl(parsed.query))

    if parsed.scheme.startswith("postgresql+asyncpg"):
        sslmode = query.pop("sslmode", None)
        if sslmode == "require":
            connect_args["ssl"] = "require"

        # asyncpg 可以用 timeout
        connect_args.setdefault("timeout", 60)

        new_query = urlencode(query)
        db_url = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment,
            )
        )

    elif parsed.scheme.startswith("sqlite+aiosqlite"):
        connect_args.setdefault("timeout", 60)

    return db_url, connect_args


DATABASE_URL, DB_CONNECT_ARGS = normalize_database_url(DATABASE_URL)


# =========================
# DATABASE
# =========================

class Base(DeclarativeBase):
    pass


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BotGroup(Base):
    __tablename__ = "bot_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    type: Mapped[str] = mapped_column(String(50), default="group")
    is_admin: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger: Mapped[str] = mapped_column(String(255), index=True)
    reply_text: Mapped[str] = mapped_column(Text, default="")
    buttons_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BannedWord(Base):
    __tablename__ = "banned_words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WelcomeSetting(Base):
    __tablename__ = "welcome_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text, default="欢迎加入")
    buttons_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AutoPost(Base):
    __tablename__ = "auto_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    cron_expr: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_timeout=60,
    connect_args=DB_CONNECT_ARGS,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


# =========================
# REDIS + MEMORY FALLBACK
# =========================

redis_client = None
memory_store: dict[str, str] = {}


async def init_redis():
    global redis_client
    if not REDIS_URL or redis is None:
        redis_client = None
        return
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping()
        print("[REDIS] connected")
    except Exception as e:
        redis_client = None
        print("[REDIS] disabled:", repr(e))


async def cache_get(key: str) -> Optional[str]:
    if redis_client:
        try:
            return await redis_client.get(key)
        except Exception:
            pass
    return memory_store.get(key)


async def cache_set(key: str, value: str, ex: Optional[int] = None):
    if redis_client:
        try:
            await redis_client.set(key, value, ex=ex)
            return
        except Exception:
            pass
    memory_store[key] = value


async def cache_delete(key: str):
    if redis_client:
        try:
            await redis_client.delete(key)
        except Exception:
            pass
    memory_store.pop(key, None)


# =========================
# GLOBAL CACHE
# =========================

admin_cache: set[int] = set()
keyword_cache: List[Keyword] = []
banned_cache: List[BannedWord] = []


# =========================
# HELPERS
# =========================

def parse_buttons(buttons_json: Optional[str]) -> Optional[types.InlineKeyboardMarkup]:
    if not buttons_json:
        return None
    try:
        data = json.loads(buttons_json)
        rows = []
        for row in data:
            btn_row = []
            for item in row:
                text = item.get("text", "").strip()
                url = item.get("url", "").strip()
                if not text or not url:
                    continue
                if not (
                    url.startswith("http://")
                    or url.startswith("https://")
                    or url.startswith("tg://")
                ):
                    continue
                btn_row.append(types.InlineKeyboardButton(text=text, url=url))
            if btn_row:
                rows.append(btn_row)
        if not rows:
            return None
        return types.InlineKeyboardMarkup(inline_keyboard=rows)
    except Exception:
        return None


async def safe_send_message(chat_id: int, text: str, reply_markup=None):
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        print("[SEND ERROR]", repr(e))


async def is_admin_user(user_id: int) -> bool:
    return user_id in admin_cache


async def load_admin_cache():
    global admin_cache
    async with SessionLocal() as db:
        rows = (await db.execute(select(AdminUser.user_id))).scalars().all()
        admin_cache = set(rows)
    print(f"[CACHE] admin_cache loaded: {len(admin_cache)}")


async def reload_keyword_cache():
    global keyword_cache
    async with SessionLocal() as db:
        keyword_cache = (
            await db.execute(select(Keyword).order_by(Keyword.id.desc()))
        ).scalars().all()
    print(f"[CACHE] keyword_cache loaded: {len(keyword_cache)}")


async def reload_banned_cache():
    global banned_cache
    async with SessionLocal() as db:
        banned_cache = (
            await db.execute(select(BannedWord).order_by(BannedWord.id.asc()))
        ).scalars().all()
    print(f"[CACHE] banned_cache loaded: {len(banned_cache)}")


async def get_all_groups():
    async with SessionLocal() as db:
        return (
            await db.execute(
                select(BotGroup).order_by(BotGroup.updated_at.desc())
            )
        ).scalars().all()


async def get_admin_groups():
    async with SessionLocal() as db:
        return (
            await db.execute(
                select(BotGroup)
                .where(BotGroup.is_admin == 1)
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


async def wait_for_db(max_retries: int = 20, delay: int = 3):
    for i in range(max_retries):
        try:
            async with engine.begin() as conn:
                await conn.execute(select(1))
            print("[DB] ready")
            return
        except Exception as e:
            print(f"[DB] waiting... ({i + 1}/{max_retries})", repr(e))
            await asyncio.sleep(delay)
    raise RuntimeError("数据库连接失败")


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[DB] tables ensured")


async def upsert_group(chat: types.Chat, is_admin: int = 0, is_active: int = 1):
    async with SessionLocal() as db:
        row = (
            await db.execute(select(BotGroup).where(BotGroup.chat_id == chat.id))
        ).scalar_one_or_none()

        title = chat.title or ""
        username = getattr(chat, "username", None)
        chat_type = chat.type

        if row:
            row.title = title
            row.username = username
            row.type = chat_type
            row.is_admin = is_admin
            row.is_active = is_active
            row.updated_at = datetime.utcnow()
        else:
            db.add(
                BotGroup(
                    chat_id=chat.id,
                    title=title,
                    username=username,
                    type=chat_type,
                    is_admin=is_admin,
                    is_active=is_active,
                    updated_at=datetime.utcnow(),
                )
            )
        await db.commit()


def web_is_authed(request: Request) -> bool:
    return request.cookies.get("admin_auth") == "1"


def require_web_auth(request: Request):
    if not web_is_authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")


# =========================
# BOT
# =========================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML"),
)

dp = Dispatcher()
router = Router()
dp.include_router(router)


@router.message(CommandStart())
async def handle_start(message: types.Message):
    await message.answer("Bot is running.")


@router.message(Command("ping"))
async def handle_ping(message: types.Message):
    await message.answer("pong")


@router.message()
async def handle_keywords(message: types.Message):
    if not message.text:
        return

    text = message.text.lower()

    # 记录群
    if message.chat.type in ("group", "supergroup"):
        await upsert_group(message.chat, is_active=1)

    # 简单屏蔽词检测
    for banned in banned_cache:
        if banned.word.lower() in text:
            await message.reply("检测到违规词。")
            return

    # 简单关键词回复
    for kw in keyword_cache:
        if kw.trigger.lower() in text:
            markup = parse_buttons(kw.buttons_json)
            await message.reply(kw.reply_text, reply_markup=markup)
            return


@router.my_chat_member()
async def track_bot_membership(event: types.ChatMemberUpdated):
    try:
        chat = event.chat
        new_status = event.new_chat_member.status
        old_status = event.old_chat_member.status

        is_active = 1
        if new_status in ("left", "kicked"):
            is_active = 0

        is_admin = 1 if new_status in ("administrator",) else 0

        await upsert_group(chat, is_admin=is_admin, is_active=is_active)

        print(
            "[BOT MEMBERSHIP]",
            {
                "chat_id": chat.id,
                "title": chat.title,
                "old_status": old_status,
                "new_status": new_status,
                "is_admin": is_admin,
                "is_active": is_active,
            },
        )
    except Exception as e:
        print("[MY_CHAT_MEMBER ERROR]", repr(e))
        traceback.print_exc()


# =========================
# FASTAPI
# =========================

app = FastAPI(title="Telegram Bot App")


@app.get("/", response_class=HTMLResponse)
async def index():
    return """
    <html>
      <body>
        <h2>Telegram Bot App</h2>
        <ul>
          <li><a href="/healthz">/healthz</a></li>
          <li><a href="/admin">/admin</a></li>
        </ul>
      </body>
    </html>
    """


@app.get("/healthz")
async def healthz():
    return {"ok": True, "time": datetime.utcnow().isoformat()}


@app.post("/webhook")
async def webhook(req: Request):
    secret = req.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if TELEGRAM_WEBHOOK_SECRET and secret != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        data = await req.json()
        update = types.Update.model_validate(data)
        await dp.feed_update(bot, update)
        return {"ok": True}
    except Exception as e:
        print("[WEBHOOK ERROR]", repr(e))
        traceback.print_exc()
        return JSONResponse(
            status_code=200,
            content={"ok": False, "error": str(e)},
        )


# =========================
# SIMPLE WEB ADMIN
# =========================

@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    if not web_is_authed(request):
        return RedirectResponse("/admin/login", status_code=status.HTTP_302_FOUND)

    admins, groups, keywords, welcomes, autos = await fetch_web_data()

    html_rows_groups = "".join(
        f"<tr><td>{g.id}</td><td>{g.chat_id}</td><td>{html.escape(g.title or '')}</td><td>{g.type}</td><td>{g.is_admin}</td><td>{g.is_active}</td></tr>"
        for g in groups
    ) or "<tr><td colspan='6'>No groups</td></tr>"

    html_rows_keywords = "".join(
        f"<tr><td>{k.id}</td><td>{html.escape(k.trigger)}</td><td>{html.escape((k.reply_text or '')[:80])}</td></tr>"
        for k in keywords
    ) or "<tr><td colspan='3'>No keywords</td></tr>"

    html_rows_admins = "".join(
        f"<tr><td>{a.id}</td><td>{a.user_id}</td></tr>"
        for a in admins
    ) or "<tr><td colspan='2'>No admins</td></tr>"

    return f"""
    <html>
      <body>
        <h2>Admin Dashboard</h2>
        <p>
          <a href="/admin/logout">Logout</a> |
          <a href="/admin/reload-caches">Reload caches</a>
        </p>

        <h3>Stats</h3>
        <ul>
          <li>Admins: {len(admins)}</li>
          <li>Groups: {len(groups)}</li>
          <li>Keywords: {len(keywords)}</li>
          <li>Welcomes: {len(welcomes)}</li>
          <li>Auto posts: {len(autos)}</li>
        </ul>

        <h3>Add Admin</h3>
        <form method="post" action="/admin/admins/add">
          <input name="user_id" placeholder="Telegram user_id" />
          <button type="submit">Add</button>
        </form>

        <h3>Add Keyword</h3>
        <form method="post" action="/admin/keywords/add">
          <input name="trigger" placeholder="trigger" />
          <input name="reply_text" placeholder="reply_text" />
          <button type="submit">Add</button>
        </form>

        <h3>Add Banned Word</h3>
        <form method="post" action="/admin/banned/add">
          <input name="word" placeholder="word" />
          <button type="submit">Add</button>
        </form>

        <h3>Admins</h3>
        <table border="1" cellpadding="6">
          <tr><th>ID</th><th>User ID</th></tr>
          {html_rows_admins}
        </table>

        <h3>Groups</h3>
        <table border="1" cellpadding="6">
          <tr><th>ID</th><th>Chat ID</th><th>Title</th><th>Type</th><th>is_admin</th><th>is_active</th></tr>
          {html_rows_groups}
        </table>

        <h3>Keywords</h3>
        <table border="1" cellpadding="6">
          <tr><th>ID</th><th>Trigger</th><th>Reply</th></tr>
          {html_rows_keywords}
        </table>
      </body>
    </html>
    """


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page():
    return """
    <html>
      <body>
        <h2>Admin Login</h2>
        <form method="post" action="/admin/login">
          <input type="password" name="key" placeholder="admin key" />
          <button type="submit">Login</button>
        </form>
      </body>
    </html>
    """


@app.post("/admin/login")
async def admin_login(key: str = Form(...)):
    if key != ADMIN_WEB_KEY:
        return HTMLResponse("<h3>Invalid key</h3>", status_code=401)

    resp = RedirectResponse("/admin", status_code=status.HTTP_302_FOUND)
    resp.set_cookie(
        "admin_auth",
        "1",
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=86400,
    )
    return resp


@app.get("/admin/logout")
async def admin_logout():
    resp = RedirectResponse("/admin/login", status_code=status.HTTP_302_FOUND)
    resp.delete_cookie("admin_auth")
    return resp


@app.get("/admin/reload-caches")
async def admin_reload_caches(request: Request):
    require_web_auth(request)
    await load_admin_cache()
    await reload_keyword_cache()
    await reload_banned_cache()
    return RedirectResponse("/admin", status_code=status.HTTP_302_FOUND)


@app.post("/admin/admins/add")
async def admin_add_admin(
    request: Request,
    user_id: int = Form(...),
):
    require_web_auth(request)
    async with SessionLocal() as db:
        exists = (
            await db.execute(select(AdminUser).where(AdminUser.user_id == user_id))
        ).scalar_one_or_none()
        if not exists:
            db.add(AdminUser(user_id=user_id))
            await db.commit()
    await load_admin_cache()
    return RedirectResponse("/admin", status_code=status.HTTP_302_FOUND)


@app.post("/admin/keywords/add")
async def admin_add_keyword(
    request: Request,
    trigger: str = Form(...),
    reply_text: str = Form(...),
):
    require_web_auth(request)
    trigger = trigger.strip()
    reply_text = reply_text.strip()
    if not trigger or not reply_text:
        raise HTTPException(status_code=400, detail="trigger/reply_text required")

    async with SessionLocal() as db:
        db.add(Keyword(trigger=trigger, reply_text=reply_text))
        await db.commit()
    await reload_keyword_cache()
    return RedirectResponse("/admin", status_code=status.HTTP_302_FOUND)


@app.post("/admin/banned/add")
async def admin_add_banned_word(
    request: Request,
    word: str = Form(...),
):
    require_web_auth(request)
    word = word.strip()
    if not word:
        raise HTTPException(status_code=400, detail="word required")

    async with SessionLocal() as db:
        exists = (
            await db.execute(select(BannedWord).where(BannedWord.word == word))
        ).scalar_one_or_none()
        if not exists:
            db.add(BannedWord(word=word))
            await db.commit()
    await reload_banned_cache()
    return RedirectResponse("/admin", status_code=status.HTTP_302_FOUND)


@app.get("/admin/api/data")
async def admin_api_data(request: Request):
    require_web_auth(request)
    admins, groups, keywords, welcomes, autos = await fetch_web_data()
    return {
        "admins": [{"id": x.id, "user_id": x.user_id} for x in admins],
        "groups": [
            {
                "id": x.id,
                "chat_id": x.chat_id,
                "title": x.title,
                "type": x.type,
                "is_admin": x.is_admin,
                "is_active": x.is_active,
                "updated_at": x.updated_at.isoformat() if x.updated_at else None,
            }
            for x in groups
        ],
        "keywords": [
            {
                "id": x.id,
                "trigger": x.trigger,
                "reply_text": x.reply_text,
            }
            for x in keywords
        ],
        "welcomes": [
            {
                "id": x.id,
                "chat_id": x.chat_id,
                "enabled": x.enabled,
                "text": x.text,
            }
            for x in welcomes
        ],
        "autos": [
            {
                "id": x.id,
                "chat_id": x.chat_id,
                "text": x.text,
                "cron_expr": x.cron_expr,
                "enabled": x.enabled,
            }
            for x in autos
        ],
    }


# =========================
# STARTUP / SHUTDOWN
# =========================

@app.on_event("startup")
async def on_startup():
    print("[APP] startup")
    await init_redis()
    await wait_for_db()
    await init_db()
    await load_admin_cache()
    await reload_keyword_cache()
    await reload_banned_cache()

    webhook_url = f"{BASE_URL}/webhook"
    try:
        result = await bot.set_webhook(
            webhook_url,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query", "my_chat_member"],
            secret_token=TELEGRAM_WEBHOOK_SECRET or None,
        )
        print("[WEBHOOK] set:", result, webhook_url)
    except Exception as e:
        print("[WEBHOOK] set failed:", repr(e))
        traceback.print_exc()


@app.on_event("shutdown")
async def on_shutdown():
    print("[APP] shutdown")
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as e:
        print("[WEBHOOK] delete failed:", repr(e))

    try:
        await bot.session.close()
    except Exception:
        pass

    try:
        await engine.dispose()
    except Exception:
        pass

    if redis_client:
        try:
            await redis_client.close()
        except Exception:
            pass


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    import uvicorn
