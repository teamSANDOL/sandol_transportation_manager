import fakeredis
import pytest
from fastapi import HTTPException, Response

from app.ratelimit import RateLimiter


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis(
        server=fakeredis.FakeServer(), decode_responses=True
    )


async def test_blocks_after_the_limit_with_retry_after(redis) -> None:
    limiter = RateLimiter(redis, 3, clock=lambda: 600.0 + 10)
    for _ in range(3):
        await limiter.hit("1.1.1.1", Response())

    with pytest.raises(HTTPException) as error:
        await limiter.hit("1.1.1.1", Response())

    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "51"


async def test_headers_show_remaining(redis) -> None:
    limiter = RateLimiter(redis, 3, clock=lambda: 600.0)
    response = Response()

    await limiter.hit("1.1.1.1", response)

    assert response.headers["X-RateLimit-Limit"] == "3"
    assert response.headers["X-RateLimit-Remaining"] == "2"


async def test_each_ip_has_its_own_counter(redis) -> None:
    limiter = RateLimiter(redis, 1, clock=lambda: 600.0)
    await limiter.hit("1.1.1.1", Response())

    await limiter.hit("2.2.2.2", Response())


async def test_window_resets_next_minute(redis) -> None:
    now = [600.0]
    limiter = RateLimiter(redis, 1, clock=lambda: now[0])
    await limiter.hit("1.1.1.1", Response())
    now[0] = 661.0

    await limiter.hit("1.1.1.1", Response())
