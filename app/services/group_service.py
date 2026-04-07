from datetime import datetime
from sqlalchemy import select

from app.db.models import BotGroup
from app.db.session import SessionLocal


async def upsert_group(chat_id: int, title: str | None, username: str | None, is_admin: bool = False):
    async with SessionLocal() as db:
        row = (await db.execute(
            select(BotGroup).where(BotGroup.chat_id == chat_id)
        )).scalar_one_or_none()

        if row:
            row.title = title
            row.username = username
            row.is_admin = is_admin
            row.updated_at = datetime.utcnow()
        else:
            row = BotGroup(
                chat_id=chat_id,
                title=title,
                username=username,
                is_admin=is_admin,
                updated_at=datetime.utcnow(),
            )
            db.add(row)

        await db.commit()
        return row


async def set_group_ai(chat_id: int, enabled: bool):
    async with SessionLocal() as db:
        row = (await db.execute(
            select(BotGroup).where(BotGroup.chat_id == chat_id)
        )).scalar_one_or_none()
        if row:
            row.ai_enabled = enabled
            row.updated_at = datetime.utcnow()
            await db.commit()
        return row


async def get_group(chat_id: int):
    async with SessionLocal() as db:
        return (await db.execute(
            select(BotGroup).where(BotGroup.chat_id == chat_id)
        )).scalar_one_or_none()


async def get_all_groups():
    async with SessionLocal() as db:
        return (await db.execute(
            select(BotGroup).order_by(BotGroup.updated_at.desc())
        )).scalars().all()
