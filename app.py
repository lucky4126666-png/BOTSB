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
    await message.answer("🤖 Bot đang hoạt động")

# ===== AUTH =====
def is_logged(request):
    return request.cookies.get("session") in sessions

# ===== LOGIN PAGE =====
async def login_page(request):
    return web.Response(text="""
    <html>
    <head>
        <title>Mars Login</title>
        <style>
            body {
                background: black;
                display:flex;
                justify-content:center;
                align-items:center;
                height:100vh;
                font-family: Arial;
                color:white;
            }
            .box {
                background: rgba(255,50,0,0.1);
                padding:40px;
                border-radius:15px;
                border:1px solid #ff3300;
                box-shadow:0 0 25px #ff3300;
            }
            input {
                display:block;
                margin:10px 0;
                padding:10px;
                width:220px;
                background:black;
                border:1px solid #ff3300;
                color:white;
            }
            button {
                padding:10px;
                width:100%;
                background:#ff3300;
                border:none;
                color:white;
                cursor:pointer;
                box-shadow:0 0 10px #ff3300;
            }
            button:hover {
                background:#ff0000;
                box-shadow:0 0 20px #ff0000;
            }
        </style>
    </head>
    <body>
        <form class="box" method="post">
            <h2>🔥 MARS LOGIN</h2>
            <input name="user" placeholder="Username"/>
            <input name="pass" type="password" placeholder="Password"/>
            <button>ENTER</button>
        </form>
    </body>
    </html>
    """, content_type="text/html")

# ===== LOGIN HANDLE =====
async def login(request):
    data = await request.post()
    user = data.get("user")
    pwd = data.get("pass")

    if user == ADMIN_USER and pwd == ADMIN_PASS:
        session_id = user + "_ok"
        sessions.add(session_id)
        res = web.HTTPFound("/")
        res.set_cookie("session", session_id)
        return res

    return web.Response(text="❌ Sai tài khoản")

# ===== LOGOUT =====
async def logout(request):
    res = web.HTTPFound("/login")
    res.del_cookie("session")
    return res

# ===== DASHBOARD =====
async def home(request):
    if not is_logged(request):
        raise web.HTTPFound("/login")

    return web.Response(text=f"""
    <html>
    <head>
        <title>Mars Dashboard</title>
        <style>
            body {{
                margin:0;
                font-family: Arial;
                background: radial-gradient(circle at top, #1a0000, #000);
                color: white;
            }}

            .container {{
                padding: 40px;
            }}

            .title {{
                color:#ff4d00;
                text-shadow:0 0 15px #ff4d00;
            }}

            .card {{
                background: rgba(255, 80, 0, 0.1);
                border: 1px solid rgba(255, 80, 0, 0.4);
                border-radius: 16px;
                padding: 30px;
                box-shadow: 0 0 25px rgba(255, 80, 0, 0.6);
                backdrop-filter: blur(10px);
                width: 320px;
            }}

            .btn {{
                display: inline-block;
                margin-top: 20px;
                padding: 10px 20px;
                border-radius: 10px;
                background: #ff4d00;
                color: white;
                text-decoration: none;
                box-shadow: 0 0 10px #ff4d00;
            }}

            .btn:hover {{
                background: #ff1a00;
                box-shadow: 0 0 20px #ff1a00;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1 class="title">🚀 MARS CONTROL PANEL</h1>

            <div class="card">
                <h2>📊 Stats</h2>
                <p>Users: {len(users)}</p>
                <p>Status: ONLINE</p>

                <a class="btn" href="/logout">Logout</a>
            </div>
        </div>
    </body>
    </html>
    """, content_type="text/html")

# ===== WEB APP =====
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
