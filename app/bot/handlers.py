from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.types import Message, ChatMemberUpdated

from app.bot.keyboards import build_buttons
from app.services.group_service import upsert_group, get_group
from app.services.keyword_service import find_keyword_reply, contains_banned_word
from app.services.welcome_service import get_welcome
from app.services.openai_service import ask_openai, save_chat_message, trim_history

router = Router()


@router.message(F.text == "/start")
async def start_cmd(message: Message):
    await message.answer("Bot đang hoạt động.")


@router.my_chat_member()
async def track_bot_membership(event: ChatMemberUpdated):
    chat = event.chat
    new_status = event.new_chat_member.status
    is_admin = new_status in ("administrator", "creator")

    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        await upsert_group(
            chat_id=chat.id,
            title=chat.title,
            username=getattr(chat, "username", None),
            is_admin=is_admin,
        )


@router.message(F.new_chat_members)
async def welcome_new_members(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    setting = await get_welcome(message.chat.id)
    if not setting or not setting.enabled:
        return

    names = ", ".join([u.full_name for u in message.new_chat_members])
    text = setting.text.replace("{name}", names).replace("{group}", message.chat.title or "")
    await message.answer(text, reply_markup=build_buttons(setting.buttons))


@router.message(F.text)
async def text_handler(message: Message):
    text = message.text.strip()

    # lưu thông tin group nếu có
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        await upsert_group(
            chat_id=message.chat.id,
            title=message.chat.title,
            username=getattr(message.chat, "username", None),
            is_admin=False,
        )

    # banned words
    banned = await contains_banned_word(text)
    if banned:
        try:
            await message.delete()
            await message.answer(f"Tin nhắn bị xoá do chứa từ cấm: {banned}")
        except Exception:
            pass
        return

    # keyword reply
    keyword = await find_keyword_reply(text)
    if keyword:
        await message.answer(
            keyword.reply,
            reply_markup=build_buttons(keyword.buttons)
        )
        return

    # private chat -> cho AI trả lời
    if message.chat.type == ChatType.PRIVATE:
        await save_chat_message(message.chat.id, message.from_user.id, "user", text)
        ai_reply = await ask_openai(message.chat.id, message.from_user.id, text)
        if ai_reply:
            await save_chat_message(message.chat.id, message.from_user.id, "assistant", ai_reply)
            await trim_history(message.chat.id, message.from_user.id)
            await message.answer(ai_reply)
        return

    # group chat -> chỉ trả lời AI khi group bật AI
    group = await get_group(message.chat.id)
    if group and group.ai_enabled:
        await save_chat_message(message.chat.id, message.from_user.id, "user", text)
        ai_reply = await ask_openai(message.chat.id, message.from_user.id, text)
        if ai_reply:
            await save_chat_message(message.chat.id, message.from_user.id, "assistant", ai_reply)
            await trim_history(message.chat.id, message.from_user.id)
            await message.reply(ai_reply)
