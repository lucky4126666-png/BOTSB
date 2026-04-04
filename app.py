import os
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, types

# ===== CONFIG =====
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "123456")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

users = {}
sessions = set()

# ===== BOT =====
@dp.message()
async def handle(message: types.Message):
    users[message.from_user.id] = True
    await message.answer("🤖 bot running")

# ===== AUTH =====
def is_logged(request):
    return request.cookies.get("session") in sessions

# ===== ROUTES =====
async def home(request):
    if not is_logged(request):
        raise web.HTTPFound("/login")
    return web.Response(text=f"""
    <h1>🚀 DASHBOARD</h1>
    <p>Users: {len(users)}</p>
    <a href="/logout">Logout</a>
    """, content_type="text/html")

async def login_page(request):
    return web.Response(text="""
    <h2>Login</h2>
    <form method="post">
        <input name="user" placeholder="user"/><br>
        <input name="pass" type="password" placeholder="pass"/><br>
        <button>Login</button>
    </form>
    """, content_type="text/html")

async def login(request):
    data = await request.post()
    user = data.get("user")
    pwd = data.get("pass")

    if user == ADMIN_USER and pwd == ADMIN_PASS:
        session_id = str(user) + "_ok"
        sessions.add(session_id)
        res = web.HTTPFound("/")
        res.set_cookie("session", session_id)
        return res

    return web.Response(text="❌ login failed")

async def logout(request):
    res = web.HTTPFound("/login")
    res.del_cookie("session")
    return res

# ===== WEB =====
app = web.Application()
app.router.add_get("/", home)
app.router.add_get("/login", login_page)
app.router.add_post("/login", login)
app.router.add_get("/logout", logout)

# ===== START BOT =====
async def start_bot(app):
    print("🔥 BOT STARTED")
    asyncio.create_task(dp.start_polling(bot))

app.on_startup.append(start_bot)

# ===== RUN =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)
