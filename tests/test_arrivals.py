import asyncio
from datetime import datetime, timedelta

import fakeredis
import pytest

from app.arrivals import (
    POLL_TTL_MARGIN,
    ArrivalService,
    Occurrence,
    ServiceUnavailable,
    StopInfo,
)
from app.config import FetchSettings, GuardSettings
from app.gbis import GbisError, GbisResponse
from app.normalize import route_name
from app.store import KST, QuotaStore
from tests.conftest import load_fixture

LIST_ROWS = load_fixture("gbis_list_25173.json")["response"]["msgBody"][
    "busArrivalList"
]
ITEM_ROWS = [
    load_fixture("gbis_item_25173_3400.json")["response"]["msgBody"]["busArrivalItem"]
]
EXCLUDED = {"22", "23", "55", "99", "123", "125"}

STOP = StopInfo(
    "25173",
    "224000513",
    True,
    tuple(
        Occurrence(route_name(r["routeName"]), str(r["routeId"]), r["staOrder"])
        for r in LIST_ROWS
        if route_name(r["routeName"]) not in EXCLUDED
    ),
)
ON_DEMAND = FetchSettings(
    on_demand_only=True,
    peak_windows=(),
    peak_seconds=0,
    offpeak_seconds=0,
    pause_windows=(),
)
GUARD = GuardSettings(30, 10, 40, "redis://unused")


class FakeGbis:
    def __init__(self, remaining: int = 5000) -> None:
        self.list_calls: list[str] = []
        self.item_calls: list[tuple] = []
        self.remaining = remaining
        self.fail = False

    async def arrival_list(self, station_id: str, key: str) -> GbisResponse:
        self.list_calls.append(key)
        await asyncio.sleep(0.01)
        if self.fail:
            raise GbisError("timeout")
        return GbisResponse(LIST_ROWS, 10000, self.remaining)

    async def arrival_item(self, station_id, route_id, sta_order, key) -> GbisResponse:
        self.item_calls.append((route_id, sta_order, key))
        return GbisResponse(ITEM_ROWS, 10000, 9000)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 3, 12, 0, tzinfo=KST)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def make(keys=("k1", "k2"), gbis=None, fetch=ON_DEMAND):
    redis = fakeredis.FakeAsyncRedis(
        server=fakeredis.FakeServer(), decode_responses=True
    )
    gbis = gbis or FakeGbis()
    clock = Clock()
    quota = QuotaStore(redis, keys, GUARD.quota_threshold)
    return ArrivalService(redis, gbis, quota, GUARD, fetch, clock), gbis, clock


async def test_second_request_within_ttl_is_served_from_cache() -> None:
    service, gbis, clock = make()

    first = await service.arrivals(STOP)
    clock.advance(39)
    second = await service.arrivals(STOP)

    assert (first.source, second.source) == ("live", "cache")
    assert len(gbis.list_calls) == 1
    assert not {a["route"] for a in second.arrivals} & EXCLUDED


async def test_expired_cache_calls_gbis_again() -> None:
    service, gbis, clock = make()
    await service.arrivals(STOP)
    clock.advance(41)

    await service.arrivals(STOP)

    assert len(gbis.list_calls) == 2


async def test_concurrent_requests_make_one_gbis_call() -> None:
    service, gbis, _ = make()

    results = await asyncio.gather(*(service.arrivals(STOP) for _ in range(20)))

    assert len(gbis.list_calls) == 1
    assert len({r.fetched_at for r in results}) == 1


async def test_gbis_failure_serves_stale_cache() -> None:
    service, gbis, clock = make()
    await service.arrivals(STOP)
    clock.advance(100)
    gbis.fail = True

    result = await service.arrivals(STOP)

    assert result.stale is True and result.source == "cache"


async def test_gbis_failure_without_cache_is_unavailable() -> None:
    service, gbis, _ = make()
    gbis.fail = True

    with pytest.raises(ServiceUnavailable):
        await service.arrivals(STOP)


async def test_no_keys_means_no_gbis_call() -> None:
    service, gbis, _ = make(keys=())

    with pytest.raises(ServiceUnavailable):
        await service.arrivals(STOP)

    assert gbis.list_calls == []


async def test_switches_to_next_key_when_remaining_hits_threshold() -> None:
    service, gbis, clock = make(gbis=FakeGbis(remaining=10))

    await service.arrivals(STOP)
    clock.advance(41)
    await service.arrivals(STOP)

    assert gbis.list_calls == ["k1", "k2"]


async def test_all_keys_exhausted_serves_stale_and_sets_guard() -> None:
    service, gbis, clock = make(keys=("k1",), gbis=FakeGbis(remaining=3))
    await service.arrivals(STOP)
    clock.advance(41)

    result = await service.arrivals(STOP)

    status = await service.status()
    assert result.stale is True and len(gbis.list_calls) == 1
    assert status["quotaGuard"] is True


async def test_route_arrival_uses_item_api_only() -> None:
    service, gbis, _ = make()

    result = await service.route_arrivals(STOP, "3400")

    assert gbis.list_calls == [] and len(gbis.item_calls) == 1
    assert {a["route"] for a in result.arrivals} == {"3400"}
    assert gbis.item_calls[0][:2] == ("224000050", 3)


async def test_poll_ignores_cache_and_never_raises() -> None:
    service, gbis, _ = make()

    await service.poll(STOP)
    await service.poll(STOP)
    gbis.fail = True
    await service.poll(STOP)

    assert len(gbis.list_calls) == 3
    assert (await service.status())["lastPolledAt"] is not None


async def test_polled_stop_ttl_follows_polling_interval() -> None:
    polling = FetchSettings(
        on_demand_only=False,
        peak_windows=(),
        peak_seconds=30,
        offpeak_seconds=60,
        pause_windows=(),
    )
    service, _, _ = make(fetch=polling)

    assert service.ttl_for(STOP) == 60 + POLL_TTL_MARGIN
