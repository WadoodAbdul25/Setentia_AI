from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sentia_sidecar.models import Base


class Database:
    def __init__(self, url: str) -> None:
        self._ensure_sqlite_directory(url)
        self.engine: AsyncEngine = create_async_engine(url, future=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _ensure_sqlite_directory(url: str) -> None:
        parsed = make_url(url)
        if not parsed.drivername.startswith("sqlite") or not parsed.database:
            return
        if parsed.database == ":memory:":
            return
        Path(parsed.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session
