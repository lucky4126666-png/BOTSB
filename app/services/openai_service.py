from sqlalchemy import select, delete
from openai import AsyncOpenAI

from app.core.config import settings
from app.db.models import ChatMemory
from app.db.session import SessionLocal

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None


async def save_chat_message(chat_id: int, user_id: int, role: str, content: str):
    async with SessionLocal() as db:
        db.add(ChatMemory(
            chat_id=chat_id,
            user_id=user_id,
            role=role,
            content=content,
        ))
        await db.commit()


async def get_recent_history(chat_id: int, user_id: int, limit: int = 10):
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(ChatMemory)
            .where(ChatMemory.chat_id == chat_id, ChatMemory.user_id == user_id)
            .order_by(ChatMemory.id.desc())
            .limit(limit)
        )).scalars().all()

    rows.reverse()
    return [{"role": r.role, "content": r.content} for r in rows]


async def trim_history(chat_id: int, user_id: int, keep_last: int = 20):
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(ChatMemory.id)
            .where(ChatMemory.chat_id == chat_id, ChatMemory.user_id == user_id)
            .order_by(ChatMemory.id.desc())
        )).scalars().all()

        if len(rows) > keep_last:
            remove_ids = rows[keep_last:]
            await db.execute(delete(ChatMemory).where(ChatMemory.id.in_(remove_ids)))
            await db.commit()


async def ask_openai(chat_id: int, user_id: int, user_text: str) -> str | None:
    if not client:
        return None

    history = await get_recent_history(chat_id, user_id, limit=8)

    messages = [
        {"role": "system", "content": settings.OPENAI_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": user_text},
    ]

    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=messages,
        temperature=0.7,
    )

    content = response.choices[0].message.content.strip() if response.choices else ""
    return content or None
