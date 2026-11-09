from datetime import datetime

import fakeredis
import pytest

from app.store import KST, QuotaStore, seconds_until_midnight

NOW = datetime(2026, 11, 3, 23, 0, tzinfo=KST)


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis(
        server=fakeredis.FakeServer(), decode_responses=True
    )


async def test_unknown_quota_is_usable_and_first_key_wins(redis) -> None:
    store = QuotaStore(redis, ("a", "b"), threshold=10)

    assert await store.pick("list") == (1, "a")


async def test_moves_to_next_key_at_or_below_threshold(redis) -> None:
    store = QuotaStore(redis, ("a", "b", "c"), threshold=10)

    await store.record("list", 1, 11, NOW)
    assert await store.pick("list") == (1, "a")
    await store.record("list", 1, 10, NOW)
    assert await store.pick("list") == (2, "b")
    await store.record("list", 2, 0, NOW)
    assert await store.pick("list") == (3, "c")


async def test_returns_none_when_every_key_is_exhausted(redis) -> None:
    store = QuotaStore(redis, ("a", "b"), threshold=10)
    for no in (1, 2):
        await store.record("list", no, 5, NOW)

    assert await store.pick("list") is None


async def test_list_and_item_are_counted_separately(redis) -> None:
    store = QuotaStore(redis, ("a", "b"), threshold=10)
    await store.record("list", 1, 0, NOW)

    assert await store.pick("item") == (1, "a")


async def test_value_expires_at_midnight_and_key_is_not_stored(redis) -> None:
    store = QuotaStore(redis, ("secret-a",), threshold=10)
    await store.record("list", 1, 500, NOW)

    assert await redis.ttl("quota:list:1") == 3600
    assert all("secret-a" not in k for k in await redis.keys("*"))
    assert (await store.snapshot())["list"] == [{"keyNo": 1, "remaining": 500}]


def test_seconds_until_midnight() -> None:
    assert seconds_until_midnight(NOW) == 3600
    assert seconds_until_midnight(datetime(2026, 11, 3, 0, 0, tzinfo=KST)) == 86400
