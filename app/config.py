"""환경 변수에서 읽는 도착정보 조회 설정."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time

from sqlalchemy.engine import URL, make_url


def _parse_hhmm(name: str, raw: str) -> time:
    if len(raw) != 4 or not raw.isdigit():
        raise ValueError(f"{name} 은 HHMM 숫자 4자리여야 한다: {raw!r}")
    hour, minute = int(raw[:2]), int(raw[2:])
    if hour > 23 or minute > 59:
        raise ValueError(f"{name} 의 시각이 범위를 벗어났다: {raw!r}")
    return time(hour, minute)


API_KEY_SLOTS = 4


def _in_window(start: time, end: time, now: time) -> bool:
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def _parse_windows(name: str, raw: str) -> tuple[tuple[time, time], ...]:
    windows = []
    for item in filter(None, (part.strip() for part in raw.split(","))):
        start, sep, end = item.partition("-")
        if not sep:
            raise ValueError(f"{name} 은 HHMM-HHMM 을 쉼표로 이은 값이다: {raw!r}")
        windows.append((_parse_hhmm(name, start), _parse_hhmm(name, end)))
    return tuple(windows)


def _parse_seconds(name: str, raw: str) -> int:
    if not raw.isdigit():
        raise ValueError(f"{name} 은 0 이상의 정수여야 한다: {raw!r}")
    return int(raw)


@dataclass(frozen=True)
class FetchSettings:
    """도착정보 조회 방식.

    Attributes:
        on_demand_only: True 면 자동 조회 없이 사용자 요청 때만 가져온다.
        peak_windows: 피크 시간대 목록. (시작, 끝) 쌍이며 끝이 이르면 자정을 넘긴다.
        peak_seconds: 피크 시간대 조회 주기(초). 0 이면 자동 조회를 하지 않는다.
        offpeak_seconds: 비피크 시간대 조회 주기(초). 0 이면 자동 조회를 하지 않는다.
        pause_windows: 조회하지 않는 시간대 목록. 형식은 peak_windows 와 같다.
    """

    on_demand_only: bool
    peak_windows: tuple[tuple[time, time], ...]
    peak_seconds: int
    offpeak_seconds: int
    pause_windows: tuple[tuple[time, time], ...]

    def is_paused(self, now: time) -> bool:
        """now 가 조회하지 않는 시간대에 드는지 돌려준다."""
        return any(_in_window(start, end, now) for start, end in self.pause_windows)

    def is_peak(self, now: time) -> bool:
        """now 가 피크 시간대에 드는지 돌려준다."""
        return any(_in_window(start, end, now) for start, end in self.peak_windows)


def load_fetch_settings(env: Mapping[str, str] = os.environ) -> FetchSettings:
    """환경 변수를 검증해서 FetchSettings 로 만든다.

    Raises:
        ValueError: 값이 형식에 맞지 않을 때.
    """
    on_demand = env.get("BUS_FETCH_ON_DEMAND_ONLY", "1")
    if on_demand not in ("0", "1"):
        raise ValueError(f"BUS_FETCH_ON_DEMAND_ONLY 는 0 또는 1 이다: {on_demand!r}")
    return FetchSettings(
        on_demand_only=on_demand == "1",
        peak_windows=_parse_windows(
            "BUS_POLL_PEAK_WINDOWS",
            env.get("BUS_POLL_PEAK_WINDOWS", "0700-1000,1600-2000"),
        ),
        peak_seconds=_parse_seconds(
            "BUS_POLL_PEAK_SECONDS", env.get("BUS_POLL_PEAK_SECONDS", "30")
        ),
        offpeak_seconds=_parse_seconds(
            "BUS_POLL_OFFPEAK_SECONDS", env.get("BUS_POLL_OFFPEAK_SECONDS", "0")
        ),
        pause_windows=_parse_windows(
            "BUS_POLL_PAUSE_WINDOWS", env.get("BUS_POLL_PAUSE_WINDOWS", "0230-0500")
        ),
    )


def load_dev_frontend_mode(env: Mapping[str, str] = os.environ) -> bool:
    """DEV_FRONTEND_MODE 가 1 이면 개발용 키오스크 화면(/demo, /static)을 켠다.

    Raises:
        ValueError: 0 또는 1 이 아닐 때.
    """
    mode = env.get("DEV_FRONTEND_MODE", "0")
    if mode not in ("0", "1"):
        raise ValueError(f"DEV_FRONTEND_MODE 는 0 또는 1 이다: {mode!r}")
    return mode == "1"


def load_api_keys(env: Mapping[str, str] = os.environ) -> tuple[str, ...]:
    """DATA_GO_KR_KEY1 부터 순서대로 읽는다. 없거나 공란인 번호는 건너뛴다."""
    keys = (
        env.get(f"DATA_GO_KR_KEY{n}", "").strip() for n in range(1, API_KEY_SLOTS + 1)
    )
    return tuple(key for key in keys if key)


def database_url(env: Mapping[str, str] = os.environ) -> URL:
    """DATABASE_URL 이 있으면 그것을, 없으면 POSTGRES_* 로 compose DB 주소를 만든다."""
    explicit = env.get("DATABASE_URL", "").strip()
    if explicit:
        return make_url(explicit)
    return URL.create(
        "postgresql+psycopg",
        username=env.get("POSTGRES_USER", "transportation"),
        password=env.get("POSTGRES_PASSWORD", ""),
        host=env.get("POSTGRES_HOST", "transportation-manager-db"),
        port=5432,
        database=env.get("POSTGRES_DB", "transportation_manager"),
    )


@dataclass(frozen=True)
class GuardSettings:
    """GBIS 호출을 막는 쪽 설정.

    Attributes:
        rate_limit_per_minute: 클라이언트 IP 당 분당 요청 수.
        quota_threshold: 키의 남은 quota 가 이 값 이하이면 다음 키로 넘긴다.
        on_demand_ttl_seconds: 요청 시 조회 cache 를 신선하다고 보는 시간(초).
        redis_url: cache, quota, rate limit 카운터를 두는 Redis 주소.
    """

    rate_limit_per_minute: int
    quota_threshold: int
    on_demand_ttl_seconds: int
    redis_url: str


def _parse_positive(name: str, raw: str) -> int:
    value = _parse_seconds(name, raw)
    if value < 1:
        raise ValueError(f"{name} 은 1 이상이어야 한다: {raw!r}")
    return value


def load_guard_settings(env: Mapping[str, str] = os.environ) -> GuardSettings:
    """환경 변수를 검증해서 GuardSettings 로 만든다.

    Raises:
        ValueError: 값이 형식에 맞지 않을 때.
    """
    return GuardSettings(
        rate_limit_per_minute=_parse_positive(
            "BUS_RATE_LIMIT_PER_MINUTE", env.get("BUS_RATE_LIMIT_PER_MINUTE", "30")
        ),
        quota_threshold=_parse_seconds(
            "BUS_QUOTA_THRESHOLD", env.get("BUS_QUOTA_THRESHOLD", "10")
        ),
        on_demand_ttl_seconds=_parse_positive(
            "BUS_ON_DEMAND_TTL_SECONDS", env.get("BUS_ON_DEMAND_TTL_SECONDS", "40")
        ),
        redis_url=env.get("REDIS_URL", "").strip()
        or "redis://transportation-manager-redis:6379/0",
    )
