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
