from sqlalchemy import select

from app.db.models import Keyword, BannedWord
from app.db.session import SessionLocal


async def get_keywords():
    async with SessionLocal() as db:
        return (await db.execute(
            select(Keyword).order_by(Keyword.id.desc())
        )).scalars().all()


async def find_keyword_reply(text: str):
    text_lower = text.lower().strip()
    async with SessionLocal() as db:
        rows = (await db.execute(select(Keyword))).scalars().all()
        for row in rows:
            if row.trigger.lower().strip() in text_lower:
                return row
    return None


async def get_banned_words():
    async with SessionLocal() as db:
        return (await db.execute(
            select(BannedWord).order_by(BannedWord.id.desc())
        )).scalars().all()


async def contains_banned_word(text: str):
    text_lower = text.lower()
    async with SessionLocal() as db:
        rows = (await db.execute(select(BannedWord))).scalars().all()
        for row in rows:
            if row.word.lower() in text_lower:
                return row.word
    return None
