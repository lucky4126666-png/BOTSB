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

# ===== DATA =====
keywords = {}
user_state = {}
sessions = set()

# ===== AUTH =====
def is_logged(request):
    return request.cookies.get("session") in sessions

# ===== BOT MENU =====
def admin_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Thêm", "📋 Danh sách")
    kb.add("❌ Xóa", "✏️ Sửa")
    kb.add("👁 Preview")
    return kb

# ===== START =====
@dp.message(commands=["start"])
async def start(message: types.Message):
    await message.answer("🔥 BOT CONTROL", reply_markup=admin_keyboard())

# ===== BOT HANDLER =====
@dp.message()
async def handle(message: types.Message):
    uid = message.from_user.id
    text = message.text

    # ===== MENU =====
    if text == "➕ Thêm":
        user_state[uid] = {"step": "keyword"}
        await message.answer("🔑 Nhập keyword:")
        return

    if text == "📋 Danh sách":
        if not keywords:
            await message.answer("❌ Chưa có keyword")
        else:
            await message.answer("\n".join(keywords.keys()))
        return

    if text == "❌ Xóa":
        user_state[uid] = {"step": "delete"}
        await message.answer("Nhập keyword cần xóa:")
        return

    if text == "✏️ Sửa":
        user_state[uid] = {"step": "edit"}
        await message.answer("Nhập keyword cần sửa:")
        return

    if text == "👁 Preview":
        user_state[uid] = {"step": "preview"}
        await message.answer("Nhập keyword:")
        return

    # ===== STATE FLOW =====
    if uid in user_state:
        state = user_state[uid]

        # DELETE
        if state["step"] == "delete":
            keywords.pop(text, None)
            user_state.pop(uid)
            await message.answer("🗑 Đã xóa")
            return

        # EDIT
        if state["step"] == "edit":
            if text in keywords:
                user_state[uid] = {"step": "keyword", "edit": text}
                await message.answer("Nhập nội dung mới:")
            else:
                await message.answer("❌ Không tồn tại")
            return

        # PREVIEW
        if state["step"] == "preview":
            data = keywords.get(text)
            if not data:
                await message.answer("❌ Không tồn tại")
                return

            markup = None
            if data["button"]:
                markup = types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("🔗 Link", url=data["button"])
                )

            if data["image"]:
                await message.answer_photo(data["image"], caption=data["text"], reply_markup=markup)
            else:
                await message.answer(data["text"], reply_markup=markup)

            user_state.pop(uid)
            return

        # ADD FLOW
        if state["step"] == "keyword":
            state["keyword"] = text.lower()
            state["step"] = "text"
            await message.answer("📝 Nhập nội dung:")
            return

        if state["step"] == "text":
            state["text"] = text
            state["step"] = "image"
            await message.answer("🖼 Gửi ảnh hoặc 'skip':")
            return

        if state["step"] == "image":
            if text == "skip":
                state["image"] = None
            elif message.photo:
                state["image"] = message.photo[-1].file_id
            else:
                await message.answer("❌ gửi ảnh hoặc skip")
                return

            state["step"] = "button"
            await message.answer("🔗 Nhập link hoặc skip:")
            return

        if state["step"] == "button":
            state["button"] = None if text == "skip" else text

            key = state.get("edit") or state["keyword"]

            keywords[key] = {
                "text": state["text"],
                "image": state["image"],
                "button": state["button"]
            }

            user_state.pop(uid)
            await message.answer(f"✅ Lưu: {key}")
            return

    # ===== AUTO REPLY =====
    key = text.lower()
    if key in keywords:
        data = keywords[key]

        markup = None
        if data["button"]:
            markup = types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("🔗 Link", url=data["button"])
            )

        if data["image"]:
            await message.answer_photo(data["image"], caption=data["text"], reply_markup=markup)
        else:
            await message.answer(data["text"], reply_markup=markup)
        return

    await message.answer("🤖 Bot đang chạy")

# ===== WEB LOGIN =====
async def login_page(request):
    return web.Response(text="""
    <html>
    <body style="background:black;color:white;display:flex;justify-content:center;align-items:center;height:100vh">
    <form method="post">
    <h2>🔥 LOGIN</h2>
    <input name="user" placeholder="user"><br>
    <input name="pass" type="password" placeholder="pass"><br>
    <button>ENTER</button>
    </form>
    </body>
    </html>
    """, content_type="text/html")

async def login(request):
    data = await request.post()
    if data.get("user") == ADMIN_USER and data.get("pass") == ADMIN_PASS:
        sid = "ok"
        sessions.add(sid)
        res = web.HTTPFound("/")
        res.set_cookie("session", sid)
        return res
    return web.Response(text="❌ sai")

async def logout(request):
    res = web.HTTPFound("/login")
    res.del_cookie("session")
    return res

# ===== DASHBOARD =====
async def home(request):
    if not is_logged(request):
        raise web.HTTPFound("/login")

    return web.Response(text=f"""
    <h1>🚀 MARS DASHBOARD</h1>
    <p>Users: {len(keywords)}</p>
    <p>Keywords: {len(keywords)}</p>
    <a href="/logout">Logout</a>
    """, content_type="text/html")

# ===== WEB =====
app = web.Application()
app.router.add_get("/", home)
app.router.add_get("/login", login_page)
app.router.add_post("/login", login)
app.router.add_get("/logout", logout)

# ===== START =====
async def start_bot(app):
    print("🔥 BOT STARTED")
    asyncio.create_task(dp.start_polling(bot))

app.on_startup.append(start_bot)

# ===== RUN =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)
