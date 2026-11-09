"""Redis 에 두는 키별 quota."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from redis.asyncio import Redis

KST = ZoneInfo("Asia/Seoul")
SERVICES = ("list", "item", "route")


def seconds_until_midnight(now: datetime) -> int:
    """다음 0시(KST)까지 남은 초. 일일 한도가 0시에 초기화된다고 가정한 값이다."""
    now = now.astimezone(KST)
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(1, int((midnight - now).total_seconds()))


class QuotaStore:
    """키 번호별 남은 quota 를 목록조회와 항목조회로 나눠 센다.

    키 값은 Redis 에 넣지 않고 번호만 쓴다.
    """

    def __init__(self, redis: Redis, keys: tuple[str, ...], threshold: int) -> None:
        self._redis = redis
        self._keys = keys
        self._threshold = threshold

    @staticmethod
    def _name(service: str, key_no: int) -> str:
        return f"quota:{service}:{key_no}"

    async def pick(self, service: str) -> tuple[int, str] | None:
        """남은 quota 가 임계값을 넘는 첫 키를 고른다. 아직 모르면 쓸 수 있다고 본다."""
        for key_no, key in enumerate(self._keys, start=1):
            remaining = await self._redis.get(self._name(service, key_no))
            if remaining is None or int(remaining) > self._threshold:
                return key_no, key
        return None

    async def record(
        self, service: str, key_no: int, remaining: int | None, now: datetime
    ) -> None:
        if remaining is None:
            return
        await self._redis.set(
            self._name(service, key_no), remaining, ex=seconds_until_midnight(now)
        )

    async def snapshot(self) -> dict[str, list[dict[str, int | None]]]:
        result: dict[str, list[dict[str, int | None]]] = {}
        for service in SERVICES:
            entries = []
            for key_no in range(1, len(self._keys) + 1):
                value = await self._redis.get(self._name(service, key_no))
                entries.append(
                    {
                        "keyNo": key_no,
                        "remaining": None if value is None else int(value),
                    }
                )
            result[service] = entries
        return result

    @property
    def key_count(self) -> int:
        return len(self._keys)
