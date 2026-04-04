import os
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

# ===== CONFIG =====
BOT_TOKEN = os.environ["BOT_TOKEN"]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== DATA =====
keywords = {}
user_state = {}

# ===== INLINE MENU =====
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Thêm", callback_data="add")],
        [InlineKeyboardButton(text="📋 Danh sách", callback_data="list")],
        [InlineKeyboardButton(text="✏️ Sửa", callback_data="edit")],
        [InlineKeyboardButton(text="❌ Xóa", callback_data="delete")],
        [InlineKeyboardButton(text="👁 Preview", callback_data="preview")]
    ])

def back_btn():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Quay lại", callback_data="back")]
    ])

# ===== START =====
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("🚀 CONTROL PANEL", reply_markup=main_menu())

# ===== CALLBACK MENU =====
@dp.callback_query()
async def menu_handler(call: types.CallbackQuery):
    uid = call.from_user.id

    if call.data == "add":
        user_state[uid] = {"step": "keyword"}
        await call.message.edit_text("🔑 Nhập keyword:", reply_markup=back_btn())

    elif call.data == "list":
        text = "\n".join(keywords.keys()) or "❌ Chưa có"
        await call.message.edit_text(f"📋 KEYWORDS:\n{text}", reply_markup=back_btn())

    elif call.data == "delete":
        user_state[uid] = {"step": "delete"}
        await call.message.edit_text("Nhập keyword cần xóa:", reply_markup=back_btn())

    elif call.data == "edit":
        user_state[uid] = {"step": "edit"}
        await call.message.edit_text("Nhập keyword cần sửa:", reply_markup=back_btn())

    elif call.data == "preview":
        user_state[uid] = {"step": "preview"}
        await call.message.edit_text("Nhập keyword:", reply_markup=back_btn())

    elif call.data == "back":
        user_state.pop(uid, None)
        await call.message.edit_text("🚀 CONTROL PANEL", reply_markup=main_menu())

# ===== TEXT / STATE =====
@dp.message()
async def handle(message: types.Message):
    uid = message.from_user.id
    text = (message.text or "").strip()

    # ===== STATE =====
    if uid in user_state:
        state = user_state[uid]

        # DELETE
        if state["step"] == "delete":
            keywords.pop(text, None)
            user_state.pop(uid)
            await message.answer("🗑 Đã xóa", reply_markup=main_menu())
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
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="🔗 Link", url=data["button"])]]
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
            if text.lower() == "skip":
                state["image"] = None
            elif message.photo:
                state["image"] = message.photo[-1].file_id
            else:
                await message.answer("❌ gửi ảnh hoặc skip")
                return

            state["step"] = "button"
            await message.answer("🔗 Nhập link hoặc 'skip':")
            return

        if state["step"] == "button":
            state["button"] = None if text.lower() == "skip" else text

            key = state.get("edit") or state["keyword"]

            keywords[key] = {
                "text": state["text"],
                "image": state["image"],
                "button": state["button"]
            }

            user_state.pop(uid)
            await message.answer(f"✅ Lưu: {key}", reply_markup=main_menu())
            return

    # ===== AUTO REPLY =====
    key = text.lower()
    if key in keywords:
        data = keywords[key]

        markup = None
        if data["button"]:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔗 Link", url=data["button"])]]
            )

        if data["image"]:
            await message.answer_photo(data["image"], caption=data["text"], reply_markup=markup)
        else:
            await message.answer(data["text"], reply_markup=markup)
        return

    await message.answer("🤖 Bot đang chạy")

# ===== WEB =====
async def index(request):
    return web.Response(text="BOT RUNNING", content_type="text/html")

app = web.Application()
app.router.add_get("/", index)

async def start_bot(app):
    print("🔥 BOT STARTED")
    asyncio.create_task(dp.start_polling(bot))

app.on_startup.append(start_bot)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)
