from sqlalchemy import select

from app.db.models import WelcomeSetting
from app.db.session import SessionLocal


async def get_welcome(chat_id: int):
    async with SessionLocal() as db:
        return (await db.execute(
            select(WelcomeSetting).where(WelcomeSetting.chat_id == chat_id)
        )).scalar_one_or_none()


async def set_welcome(chat_id: int, text: str, enabled: bool = True, buttons=None):
    async with SessionLocal() as db:
        row = (await db.execute(
            select(WelcomeSetting).where(WelcomeSetting.chat_id == chat_id)
        )).scalar_one_or_none()

        if row:
            row.text = text
            row.enabled = enabled
            row.buttons = buttons
        else:
            row = WelcomeSetting(
                chat_id=chat_id,
                text=text,
                enabled=enabled,
                buttons=buttons,
            )
            db.add(row)

        await db.commit()
        return row
