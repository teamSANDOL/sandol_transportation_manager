"""시간대 설정대로 자동 조회 대상 정류장을 주기적으로 부른다."""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime

from app.arrivals import ArrivalService, StopInfo, now_kst
from app.config import FetchSettings

log = logging.getLogger(__name__)


def interval_for(settings: FetchSettings, now: datetime) -> int:
    """지금 적용할 조회 주기(초). 0 이면 자동 조회를 하지 않는다.

    가져오지 않는 시간대가 피크보다 앞선다. 비피크가 요청 시 조회뿐이면 0 이다.
    """
    if settings.on_demand_only or settings.is_paused(now.time()):
        return 0
    return (
        settings.peak_seconds
        if settings.is_peak(now.time())
        else settings.offpeak_seconds
    )


class Poller:
    def __init__(
        self,
        service: ArrivalService,
        stops: list[StopInfo],
        settings: FetchSettings,
        clock: Callable[[], datetime] = now_kst,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service = service
        self._stops = stops
        self._settings = settings
        self._clock = clock
        self._monotonic = monotonic
        self._last: dict[str, float] = {}

    async def tick(self) -> int:
        """주기가 된 정류장을 한 번씩 부르고 부른 수를 돌려준다."""
        interval = interval_for(self._settings, self._clock())
        if interval == 0:
            return 0
        called = 0
        for stop in self._stops:
            now = self._monotonic()
            last = self._last.get(stop.station_number)
            if last is None or now - last >= interval:
                self._last[stop.station_number] = now
                await self._service.poll(stop)
                called += 1
        return called

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001  조회 루프는 어떤 오류에도 계속 돈다.
                log.exception("자동 조회 루프 오류")
            await asyncio.sleep(1)
