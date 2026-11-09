"""산돌이 교통정보 서비스의 FastAPI 진입점."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from sqlalchemy import select

from app.arrivals import ArrivalService, Occurrence, StopInfo
from app.arrivals_api import router as arrivals_router
from app.arrivals_api import status_router
from app.config import (
    load_api_keys,
    load_dev_frontend_mode,
    load_fetch_settings,
    load_guard_settings,
)
from app.db import create_session_factory
from app.demo import WEB_DIR
from app.demo import router as demo_router
from app.gbis import GbisClient
from app.models import Stop
from app.poller import Poller
from app.ratelimit import RateLimiter
from app.stops import LOAD_ROUTES
from app.stops import router as stops_router
from app.store import QuotaStore


async def _polled_stops(app: FastAPI) -> list[StopInfo]:
    async with app.state.session_factory() as session:
        stops = await session.scalars(
            select(Stop).options(LOAD_ROUTES).where(Stop.polled).order_by(Stop.priority)
        )
        return [
            StopInfo(
                s.station_number,
                s.station_id,
                s.polled,
                tuple(
                    Occurrence(link.route.name, link.gbis_route_id, link.sta_order)
                    for link in s.stop_routes
                ),
            )
            for s in stops.all()
        ]


def create_app(
    redis_client: Redis | None = None, http_client: httpx.AsyncClient | None = None
) -> FastAPI:
    """FastAPI 앱을 만든다.

    Args:
        redis_client: 테스트에서 넣는 Redis. 없으면 REDIS_URL 로 만든다.
        http_client: 테스트에서 넣는 GBIS 용 HTTP 클라이언트.

    Returns:
        `/health`, `/stops`, 도착정보를 가진 FastAPI 앱.
    """
    guard = load_guard_settings()
    fetch = load_fetch_settings()
    redis = redis_client or Redis.from_url(guard.redis_url, decode_responses=True)
    http = http_client or httpx.AsyncClient()
    quota = QuotaStore(redis, load_api_keys(), guard.quota_threshold)
    service = ArrivalService(redis, GbisClient(http), quota, guard, fetch)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        poller_task = None
        if not fetch.on_demand_only:
            poller = Poller(service, await _polled_stops(app), fetch)
            poller_task = asyncio.create_task(poller.run())
        yield
        if poller_task is not None:
            poller_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller_task
        await http.aclose()
        await redis.aclose()

    app = FastAPI(title="sandol-transportation-manager", lifespan=lifespan)
    app.state.session_factory = create_session_factory()
    app.state.service = service
    app.state.limiter = RateLimiter(redis, guard.rate_limit_per_minute)
    app.include_router(stops_router)
    app.include_router(arrivals_router)
    # 꺼져 있으면 화면, static, /status(키별 남은 quota)를 등록하지 않아 404 가 된다.
    if load_dev_frontend_mode():
        app.include_router(status_router)
        app.include_router(demo_router)
        app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
