from datetime import datetime, time

from app.config import FetchSettings
from app.poller import Poller, interval_for
from app.store import KST

PEAK = ((time(7, 0), time(10, 0)),)
PAUSE = ((time(2, 30), time(5, 0)),)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 3, hour, minute, tzinfo=KST)


def settings(on_demand: bool = False, offpeak: int = 0) -> FetchSettings:
    return FetchSettings(
        on_demand_only=on_demand,
        peak_windows=PEAK,
        peak_seconds=30,
        offpeak_seconds=offpeak,
        pause_windows=PAUSE,
    )


def test_on_demand_only_never_polls() -> None:
    assert interval_for(settings(on_demand=True), at(8)) == 0


def test_peak_offpeak_and_pause() -> None:
    cfg = settings(offpeak=120)

    assert interval_for(cfg, at(8)) == 30
    assert interval_for(cfg, at(12)) == 120
    assert interval_for(cfg, at(3)) == 0


def test_zero_offpeak_means_no_polling_outside_peak() -> None:
    assert interval_for(settings(offpeak=0), at(12)) == 0


def test_pause_wins_over_peak() -> None:
    cfg = FetchSettings(
        on_demand_only=False,
        peak_windows=((time(2, 0), time(4, 0)),),
        peak_seconds=30,
        offpeak_seconds=60,
        pause_windows=PAUSE,
    )

    assert interval_for(cfg, at(3)) == 0


class FakeService:
    def __init__(self) -> None:
        self.polled: list[str] = []

    async def poll(self, stop) -> None:
        self.polled.append(stop.station_number)


class Stop:
    def __init__(self, number: str) -> None:
        self.station_number = number


async def test_tick_polls_each_stop_once_per_interval() -> None:
    service = FakeService()
    mono = [100.0]
    poller = Poller(
        service,  # type: ignore[arg-type]
        [Stop("1"), Stop("2")],  # type: ignore[list-item]
        settings(),
        clock=lambda: at(8),
        monotonic=lambda: mono[0],
    )

    assert await poller.tick() == 2
    mono[0] += 10
    assert await poller.tick() == 0
    mono[0] += 25
    assert await poller.tick() == 2
    assert service.polled == ["1", "2", "1", "2"]


async def test_tick_does_nothing_in_pause() -> None:
    service = FakeService()
    poller = Poller(service, [Stop("1")], settings(), clock=lambda: at(3))  # type: ignore

    assert await poller.tick() == 0 and service.polled == []
