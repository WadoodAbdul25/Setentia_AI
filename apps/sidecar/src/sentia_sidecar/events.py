from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sentia_sidecar.models import EventRecord
from sentia_sidecar.protocol import EventEnvelope


class EventStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def publish(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        conversation_id: str | None = None,
        run_id: str | None = None,
    ) -> EventEnvelope:
        record = EventRecord(
            event_id=f"evt_{uuid4().hex}",
            conversation_id=conversation_id,
            run_id=run_id,
            event_type=event_type,
            payload=payload or {},
            created_at=datetime.now(UTC),
        )
        async with self._sessions.begin() as session:
            session.add(record)
            await session.flush()
            envelope = self._to_envelope(record)
        return envelope

    async def replay_after(self, sequence: int) -> list[EventEnvelope]:
        async with self._sessions() as session:
            result = await session.scalars(
                select(EventRecord)
                .where(EventRecord.sequence > sequence)
                .order_by(EventRecord.sequence.asc())
            )
            return [self._to_envelope(record) for record in result]

    @staticmethod
    def _to_envelope(record: EventRecord) -> EventEnvelope:
        return EventEnvelope(
            event_id=record.event_id,
            sequence=record.sequence,
            conversation_id=record.conversation_id,
            run_id=record.run_id,
            type=record.event_type,
            created_at=record.created_at,
            payload=record.payload,
        )
