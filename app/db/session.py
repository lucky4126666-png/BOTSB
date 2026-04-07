from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.config import settings

DATABASE_URL, DB_CONNECT_ARGS = settings.db_url_and_args

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_timeout=60,
    connect_args=DB_CONNECT_ARGS,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
```

---

# 8. `app/core/cache.py`

```python
import json
from typing import Any, Optional

try:
    import redis.asyncio as redis
except Exception:
    redis = None

from app.core.config import settings


class MemoryFallbackCache:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> Optional[str]:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value

    async def delete(self, key: str):
        self.store.pop(key, None)


class Cache:
    def __init__(self):
        self.client = None
        self.fallback = MemoryFallbackCache()

    async def connect(self):
        if settings.REDIS_URL and redis:
            try:
                self.client = redis.from_url(settings.REDIS_URL, decode_responses=True)
                await self.client.ping()
                print("[CACHE] Redis connected")
            except Exception as e:
                print("[CACHE] Redis failed, fallback RAM:", repr(e))
                self.client = None
        else:
            print("[CACHE] Using RAM fallback")

    async def get_json(self, key: str, default: Any = None):
        raw = None
        if self.client:
            raw = await self.client.get(key)
        else:
            raw = await self.fallback.get(key)

        if raw is None:
            return default
        try:
            return json.loads(raw)
        except Exception:
            return default

    async def set_json(self, key: str, value: Any, ex: int | None = None):
        raw = json.dumps(value, ensure_ascii=False)
        if self.client:
            await self.client.set(key, raw, ex=ex)
        else:
            await self.fallback.set(key, raw, ex=ex)

    async def delete(self, key: str):
        if self.client:
            await self.client.delete(key)
        else:
            await self.fallback.delete(key)


cache = Cache()
