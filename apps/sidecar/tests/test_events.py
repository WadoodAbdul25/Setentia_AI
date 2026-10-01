from datetime import UTC, datetime, timedelta, timezone

from sentia_sidecar.database import Database
from sentia_sidecar.events import EventStore
from sentia_sidecar.models import EventRecord


async def test_sqlite_replay_preserves_wire_utc_timestamp(database_url):
    database = Database(database_url)
    await database.initialize()
    try:
        store = EventStore(database.sessions)
        published = await store.publish("test.event", {"message": "test"})
        replayed = (await store.replay_after(0))[0]
        assert replayed.created_at == published.created_at
        assert replayed.model_dump(mode="json", by_alias=True)["createdAt"].endswith("Z")
    finally:
        await database.close()


def test_event_offset_is_normalized_without_changing_instant():
    timestamp = datetime(2026, 9, 21, 14, 16, tzinfo=timezone(timedelta(hours=-4)))
    envelope = EventStore._to_envelope(
        EventRecord(
            event_id="evt_test",
            sequence=1,
            event_type="test.event",
            payload={},
            created_at=timestamp,
        )
    )
    assert envelope.created_at == timestamp
    assert envelope.created_at.tzinfo == UTC
    assert envelope.model_dump(mode="json", by_alias=True)["createdAt"] == "2026-09-21T18:16:00Z"
