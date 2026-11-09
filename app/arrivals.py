"""도착정보 조회: cache, 동시 요청 합치기, 키 선택을 거쳐서만 GBIS 를 부른다."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import FetchSettings, GuardSettings
from app.gbis import GbisClient, GbisError
from app.normalize import normalize
from app.store import KST, QuotaStore

log = logging.getLogger(__name__)

# 만료된 cache 도 GBIS 실패 때 stale 로 줄 수 있게 이만큼 남긴다.
CACHE_KEEP_SECONDS = 3600
# 노선의 정류소 순서는 거의 바뀌지 않는다. 하루에 한 번만 다시 받는다.
ROUTE_STATIONS_TTL = 86400
POLL_TTL_MARGIN = 10


class ServiceUnavailable(Exception):
    """GBIS 를 부를 수 없고 쓸 cache 도 없다."""


@dataclass(frozen=True)
class Occurrence:
    """노선이 정류장에 서는 한 번(회차 전후는 따로)이다."""

    route: str
    gbis_route_id: str
    sta_order: int


@dataclass(frozen=True)
class StopInfo:
    station_number: str
    station_id: str
    polled: bool
    occurrences: tuple[Occurrence, ...]

    @property
    def route_names(self) -> dict[str, str]:
        return {o.gbis_route_id: o.route for o in self.occurrences}


@dataclass(frozen=True)
class ArrivalResult:
    fetched_at: datetime
    source: str
    stale: bool
    ttl_seconds: int
    arrivals: list[dict[str, Any]]


def _item_name(stop: StopInfo, occurrence: Occurrence) -> str:
    return (
        f"item:{stop.station_number}:{occurrence.gbis_route_id}:{occurrence.sta_order}"
    )


def now_kst() -> datetime:
    return datetime.now(KST)


class ArrivalService:
    def __init__(
        self,
        redis: Redis,
        gbis: GbisClient,
        quota: QuotaStore,
        guard: GuardSettings,
        fetch: FetchSettings,
        clock: Callable[[], datetime] = now_kst,
    ) -> None:
        self._redis = redis
        self._gbis = gbis
        self._quota = quota
        self._guard = guard
        self._fetch = fetch
        self._clock = clock
        self._inflight: dict[str, asyncio.Future[dict[str, Any]]] = {}

    def ttl_for(self, stop: StopInfo) -> int:
        """자동 조회 중인 정류장은 조회 주기, 나머지는 요청 시 조회 TTL 을 쓴다."""
        if stop.polled and not self._fetch.on_demand_only:
            now = self._clock().time()
            interval = (
                0
                if self._fetch.is_paused(now)
                else self._fetch.peak_seconds
                if self._fetch.is_peak(now)
                else self._fetch.offpeak_seconds
            )
            if interval:
                # 여유가 없으면 다음 자동 조회 직전의 요청이 GBIS 를 또 부른다.
                return interval + POLL_TTL_MARGIN
        return self._guard.on_demand_ttl_seconds

    async def arrivals(self, stop: StopInfo) -> ArrivalResult:
        """cache 가 신선하면 그것을, 아니면 GBIS 를 한 번만 불러서 준다."""
        ttl = self.ttl_for(stop)
        name = f"arrivals:{stop.station_number}"
        return await self._serve(name, ttl, lambda: self._fetch_list(stop, "on_demand"))

    async def route_arrivals(self, stop: StopInfo, route: str) -> ArrivalResult:
        """노선 하나를 항목조회로 본다. 목록조회 cache 와 quota 를 쓰지 않는다."""
        ttl = self._guard.on_demand_ttl_seconds
        parts = [
            await self._serve(
                _item_name(stop, o),
                ttl,
                lambda o=o: self._fetch_item(stop, o),
            )
            for o in stop.occurrences
            if o.route == route
        ]
        oldest = min(parts, key=lambda p: p.fetched_at)
        return ArrivalResult(
            fetched_at=oldest.fetched_at,
            source="cache" if all(p.source == "cache" for p in parts) else "live",
            stale=any(p.stale for p in parts),
            ttl_seconds=ttl,
            arrivals=[a for p in parts for a in p.arrivals],
        )

    async def route_stations(self, gbis_route_id: str) -> list[dict[str, Any]]:
        """노선의 정류소 순서. 하루 cache 를 쓰고 실패하면 만료된 cache 를 준다."""
        name = f"stations:{gbis_route_id}"
        try:
            cached = await self._read(name)
            if cached is not None and self._age(cached) <= ROUTE_STATIONS_TTL:
                return cached["stations"]
            try:
                fresh = await self._coalesce(
                    name, lambda: self._fetch_stations(name, gbis_route_id)
                )
                return fresh["stations"]
            except (GbisError, ServiceUnavailable) as error:
                log.warning("노선 정류소 조회 실패 %s: %s", name, error)
                if cached is not None:
                    return cached["stations"]
                raise ServiceUnavailable(str(error)) from None
        except RedisError:
            raise ServiceUnavailable("redis") from None

    async def _fetch_stations(self, name: str, gbis_route_id: str) -> dict[str, Any]:
        picked = await self._quota.pick("route")
        if picked is None:
            raise ServiceUnavailable("no key with quota")
        key_no, key = picked
        response = await self._gbis.route_stations(gbis_route_id, key)
        await self._quota.record("route", key_no, response.remaining, self._clock())
        stations = sorted(
            (
                {
                    "seq": row["stationSeq"],
                    "name": str(row.get("stationName", "")),
                    "turn": row.get("turnYn") == "Y",
                }
                for row in response.rows
                if isinstance(row.get("stationSeq"), int)
            ),
            key=lambda s: s["seq"],
        )
        if not stations:
            raise ServiceUnavailable("empty route stations")
        payload = {"fetchedAt": self._clock().isoformat(), "stations": stations}
        await self._redis.set(name, json.dumps(payload), ex=ROUTE_STATIONS_TTL * 2)
        return payload

    async def poll(self, stop: StopInfo) -> None:
        """자동 조회 한 번. cache 를 무시하고 부르며 실패해도 예외를 내지 않는다."""
        try:
            await self._coalesce(
                f"arrivals:{stop.station_number}",
                lambda: self._fetch_list(stop, "polling"),
            )
            await self._redis.set("poll:last", self._clock().isoformat())
        except (GbisError, ServiceUnavailable, RedisError) as error:
            log.warning("자동 조회 실패 %s: %s", stop.station_number, error)

    async def status(self) -> dict[str, Any]:
        snapshot = await self._quota.snapshot()
        last = await self._redis.get("poll:last")
        guard = await self._quota.pick("list") is None
        return {
            "fetchMode": "on_demand" if self._fetch.on_demand_only else "polling",
            "keysConfigured": self._quota.key_count,
            "quota": snapshot,
            "quotaGuard": guard,
            "lastPolledAt": last,
        }

    async def _serve(
        self,
        name: str,
        ttl: int,
        fetch: Callable[[], Awaitable[dict[str, Any]]],
    ) -> ArrivalResult:
        try:
            cached = await self._read(name)
            if cached is not None and self._age(cached) <= ttl:
                return self._result(cached, "cache", False, ttl)
            try:
                fresh = await self._coalesce(name, fetch)
                return self._result(fresh, "live", False, ttl)
            except (GbisError, ServiceUnavailable) as error:
                log.warning("GBIS 조회 실패 %s: %s", name, error)
                if cached is not None:
                    return self._result(cached, "cache", True, ttl)
                raise ServiceUnavailable(str(error)) from None
        except RedisError:
            raise ServiceUnavailable("redis") from None

    async def _coalesce(
        self, name: str, fetch: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        """같은 대상의 동시 요청을 호출 한 번으로 합친다. 한 프로세스 안에서만 맞다."""
        future = self._inflight.get(name)
        if future is None:
            future = asyncio.ensure_future(fetch())
            self._inflight[name] = future
            future.add_done_callback(lambda _: self._inflight.pop(name, None))
        return await asyncio.shield(future)

    async def _fetch_list(self, stop: StopInfo, trigger: str) -> dict[str, Any]:
        picked = await self._quota.pick("list")
        if picked is None:
            raise ServiceUnavailable("no key with quota")
        key_no, key = picked
        response = await self._gbis.arrival_list(stop.station_id, key)
        await self._quota.record("list", key_no, response.remaining, self._clock())
        arrivals = normalize(response.rows, stop.route_names)
        return await self._store(f"arrivals:{stop.station_number}", arrivals)

    async def _fetch_item(
        self, stop: StopInfo, occurrence: Occurrence
    ) -> dict[str, Any]:
        picked = await self._quota.pick("item")
        if picked is None:
            raise ServiceUnavailable("no key with quota")
        key_no, key = picked
        response = await self._gbis.arrival_item(
            stop.station_id, occurrence.gbis_route_id, occurrence.sta_order, key
        )
        await self._quota.record("item", key_no, response.remaining, self._clock())
        arrivals = normalize(
            response.rows, {occurrence.gbis_route_id: occurrence.route}
        )
        return await self._store(_item_name(stop, occurrence), arrivals)

    async def _store(self, name: str, arrivals: list[Any]) -> dict[str, Any]:
        payload = {
            "fetchedAt": self._clock().isoformat(),
            "arrivals": [asdict(a) for a in arrivals],
        }
        await self._redis.set(name, json.dumps(payload), ex=CACHE_KEEP_SECONDS)
        return payload

    async def _read(self, name: str) -> dict[str, Any] | None:
        raw = await self._redis.get(name)
        return None if raw is None else json.loads(raw)

    def _age(self, payload: dict[str, Any]) -> float:
        fetched = datetime.fromisoformat(payload["fetchedAt"])
        return (self._clock() - fetched).total_seconds()

    @staticmethod
    def _result(
        payload: dict[str, Any], source: str, stale: bool, ttl: int
    ) -> ArrivalResult:
        return ArrivalResult(
            fetched_at=datetime.fromisoformat(payload["fetchedAt"]),
            source=source,
            stale=stale,
            ttl_seconds=ttl,
            arrivals=payload["arrivals"],
        )
