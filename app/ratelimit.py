"""클라이언트 IP 별 분당 요청 제한. 인증이 없어서 GBIS quota 를 지키는 최소 장치다."""

import time
from collections.abc import Callable

from fastapi import HTTPException, Request, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError

WINDOW_SECONDS = 60


def client_ip(request: Request) -> str:
    """gateway 가 넘기는 X-Real-IP 를 쓴다. 서비스 포트는 127.0.0.1 에만 열려 있다."""
    forwarded = request.headers.get("x-real-ip", "").strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(
        self,
        redis: Redis,
        per_minute: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._per_minute = per_minute
        self._clock = clock

    async def hit(self, ip: str, response: Response) -> None:
        now = self._clock()
        window = int(now // WINDOW_SECONDS)
        retry_after = int(WINDOW_SECONDS - now % WINDOW_SECONDS) + 1
        name = f"rl:{ip}:{window}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(name)
                pipe.expire(name, WINDOW_SECONDS + 10)
                count = (await pipe.execute())[0]
        except RedisError:
            # 막는 쪽이 죽었으면 GBIS 로 나가는 요청도 막는다.
            raise HTTPException(
                status_code=503, detail="잠시 뒤 다시 시도한다."
            ) from None
        headers = {
            "X-RateLimit-Limit": str(self._per_minute),
            "X-RateLimit-Remaining": str(max(0, self._per_minute - count)),
        }
        if count > self._per_minute:
            headers["Retry-After"] = str(retry_after)
            raise HTTPException(
                status_code=429,
                detail="요청이 너무 많다. 잠시 뒤 다시 시도한다.",
                headers=headers,
            )
        response.headers.update(headers)


async def rate_limit(request: Request, response: Response) -> None:
    await request.app.state.limiter.hit(client_ip(request), response)
