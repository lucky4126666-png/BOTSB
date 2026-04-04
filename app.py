import os
import json
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import OpenAI

# ===== CONFIG =====
BOT_TOKEN = os.environ["BOT_TOKEN"]
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
client = OpenAI(api_key=OPENAI_API_KEY)

DATA_FILE = "data.json"

# ===== LOAD DATA =====
import tempfile

def save_data():
    try:
        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            json.dump(keywords, tmp, ensure_ascii=False, indent=2)
            temp_name = tmp.name

        os.replace(temp_name, DATA_FILE)

    except Exception as e:
        print("SAVE ERROR:", e)
        keywords = load_data()

if not isinstance(keywords, dict):
    keywords = {}
# ===== BUTTON =====
def build_buttons(btn_text):
    if not btn_text:
        return None

    buttons = []
    row = []

    for line in btn_text.split("\n"):
        if "|" not in line:
            continue

        name, url = line.split("|", 1)
        name = name.strip()
        url = url.strip()

        if not url.startswith("http"):
            continue

        row.append(InlineKeyboardButton(text=name, url=url))

        if len(row) == 2:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ===== MENU =====
def menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Thêm", callback_data="add")],
        [InlineKeyboardButton(text="📋 Danh sách", callback_data="list")],
        [InlineKeyboardButton(text="✏️ Sửa", callback_data="edit")],
        [InlineKeyboardButton(text="❌ Xóa", callback_data="delete")],
        [InlineKeyboardButton(text="👁 Preview", callback_data="preview")]
    ])

def back():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Quay lại", callback_data="back")]
    ])

# ===== AI =====
async def ask_ai(text):
    try:
        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Bạn là bot Telegram thông minh, trả lời ngắn gọn."},
                {"role": "user", "content": text}
            ]
        )
        return res.choices[0].message.content
    except:
        return "🤖 AI lỗi"

# ===== START =====
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("🚀 CONTROL PANEL", reply_markup=menu())

# ===== CALLBACK =====
@dp.callback_query()
async def callback(call: types.CallbackQuery):
    uid = call.from_user.id

    if call.data == "add":
        user_state[uid] = {"step": "keyword"}
        await call.message.edit_text("🔑 Nhập keyword", reply_markup=back())

    elif call.data == "list":
        txt = "\n".join(keywords.keys()) or "❌ Chưa có"
        await call.message.edit_text(txt, reply_markup=back())

    elif call.data == "delete":
        user_state[uid] = {"step": "delete"}
        await call.message.edit_text("Nhập keyword cần xóa", reply_markup=back())

    elif call.data == "edit":
        user_state[uid] = {"step": "edit"}
        await call.message.edit_text("Nhập keyword cần sửa", reply_markup=back())

    elif call.data == "preview":
        user_state[uid] = {"step": "preview"}
        await call.message.edit_text("Nhập keyword", reply_markup=back())

    elif call.data == "back":
        user_state.pop(uid, None)
        await call.message.edit_text("🚀 CONTROL PANEL", reply_markup=menu())

# ===== HANDLE =====
@dp.message()
async def handle(message: types.Message):
    uid = message.from_user.id
    text = (message.text or "").strip()

    if uid in user_state:
        state = user_state[uid]

        # DELETE
        if state["step"] == "delete":
            keywords.pop(text.lower(), None)
            save_data()
            user_state.pop(uid)
            await message.answer("🗑 Đã xóa", reply_markup=menu())
            return

        # EDIT
        if state["step"] == "edit":
            if text.lower() in keywords:
                user_state[uid] = {"step": "keyword", "edit": text.lower()}
                await message.answer("Nhập nội dung mới")
            else:
                await message.answer("❌ Không tồn tại")
            return

        # PREVIEW
        if state["step"] == "preview":
            key = text.lower()
            data = keywords.get(key)

            if not data:
                await message.answer("❌ Không tồn tại")
                return

            markup = build_buttons(data.get("button"))

            if data.get("image"):
                msg = await message.answer_photo(data["image"], caption=data["text"], reply_markup=markup)
            else:
                msg = await message.answer(data["text"], reply_markup=markup)

            await asyncio.sleep(5)
            try:
                await msg.delete()
            except:
                pass

            user_state.pop(uid)
            return

        # ADD FLOW
        if state["step"] == "keyword":
            state["keyword"] = text.lower()
            state["step"] = "text"
            await message.answer("Nhập nội dung")
            return

        if state["step"] == "text":
            state["text"] = text
            state["step"] = "image"
            await message.answer("Gửi ảnh hoặc 'skip'")
            return

        if state["step"] == "image":
            if text.lower() == "skip":
                state["image"] = None
            elif message.photo:
                state["image"] = message.photo[-1].file_id
            else:
                return await message.answer("❌ gửi ảnh hoặc skip")

            state["step"] = "button"
            await message.answer("Nhập nút (mỗi dòng: tên | link)")
            return

        if state["step"] == "button":
            state["button"] = None if text.lower() == "skip" else text

            key = state.get("edit") or state["keyword"]

            keywords[key] = {
                "text": state["text"],
                "image": state["image"],
                "button": state["button"]
            }

            save_data()
            user_state.pop(uid)
            await message.answer("✅ Lưu xong", reply_markup=menu())
            return

    # ===== KEYWORD =====
    key = text.lower()

    if key in keywords:
        data = keywords[key]
        markup = build_buttons(data.get("button"))

        if data.get("image"):
            await message.answer_photo(data["image"], caption=data["text"], reply_markup=markup)
        else:
            await message.answer(data["text"], reply_markup=markup)
        return

    # ===== AI =====
    reply = await ask_ai(text)
    await message.answer(reply)

# ===== WEB =====
async def index(request):
    return web.Response(text="BOT OK")

app = web.Application()
app.router.add_get("/", index)

async def start_bot(app):
    print("🔥 BOT STARTED")
    asyncio.create_task(dp.start_polling(bot))

app.on_startup.append(start_bot)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
