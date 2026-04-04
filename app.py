import os
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, types

BOT_TOKEN = os.environ["BOT_TOKEN"]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

users = {}

@dp.message()
async def handle(message: types.Message):
    text = message.text.lower()

    if text == "ping":
        await message.answer("pong 🏓")
    else:
        await message.answer("🤖 running")

async def index(request):
    return web.Response(text="BOT OK", content_type="text/html")

app = web.Application()
app.router.add_get("/", index)

async def start_bot(app):
    print("🔥 BOT STARTED")
    asyncio.create_task(dp.start_polling(bot))

app.on_startup.append(start_bot)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)
