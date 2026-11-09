"""도착정보와 상태 API. GBIS 로 나갈 수 있는 경로에는 rate limit 을 건다."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.arrivals import ArrivalResult, Occurrence, ServiceUnavailable, StopInfo
from app.db import get_session
from app.ratelimit import rate_limit
from app.stops import Camel, StationNumber, find_stop

router = APIRouter(prefix="/stops", dependencies=[Depends(rate_limit)])
status_router = APIRouter(dependencies=[Depends(rate_limit)])

RouteName = Annotated[str, Path(pattern=r"^[0-9A-Za-z-]{1,10}$")]


class BusOut(Camel):
    predict_sec: int | None
    predict_minutes: int | None
    plate_no: str | None
    location_no: int | None
    remain_seat_cnt: int | None
    crowded: int | None
    low_plate: int | None


class ArrivalOut(Camel):
    route: str
    dest_name: str
    sta_order: int
    state: Literal["running", "wait", "none"]
    buses: list[BusOut]


class ArrivalsOut(Camel):
    station_number: str
    fetched_at: datetime
    source: Literal["live", "cache"]
    stale: bool
    ttl_seconds: int
    arrivals: list[ArrivalOut]


async def _stop_info(session: AsyncSession, station_number: str) -> StopInfo:
    stop = await find_stop(session, station_number)
    if stop is None:
        raise HTTPException(status_code=404, detail="없는 정류소 번호다.")
    return StopInfo(
        station_number=stop.station_number,
        station_id=stop.station_id,
        polled=stop.polled,
        occurrences=tuple(
            Occurrence(link.route.name, link.gbis_route_id, link.sta_order)
            for link in stop.stop_routes
        ),
    )


def _out(station_number: str, result: ArrivalResult) -> ArrivalsOut:
    return ArrivalsOut(
        station_number=station_number,
        fetched_at=result.fetched_at,
        source=result.source,  # type: ignore[arg-type]
        stale=result.stale,
        ttl_seconds=result.ttl_seconds,
        arrivals=[ArrivalOut.model_validate(a) for a in result.arrivals],
    )


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="도착 정보를 가져오지 못했다.")


@router.get("/{station_number}/arrivals", response_model=ArrivalsOut)
async def arrivals(
    station_number: StationNumber,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    route: Annotated[str | None, Query(pattern=r"^[0-9A-Za-z-]{1,10}$")] = None,
) -> ArrivalsOut:
    stop = await _stop_info(session, station_number)
    if route is not None and route not in {o.route for o in stop.occurrences}:
        raise HTTPException(status_code=404, detail="그 정류장에 서지 않는 노선이다.")
    try:
        result = await request.app.state.service.arrivals(stop)
    except ServiceUnavailable:
        raise _unavailable() from None
    if route is not None:
        result = ArrivalResult(
            result.fetched_at,
            result.source,
            result.stale,
            result.ttl_seconds,
            [a for a in result.arrivals if a["route"] == route],
        )
    return _out(station_number, result)


@router.get("/{station_number}/routes/{route}/arrival", response_model=ArrivalsOut)
async def route_arrival(
    station_number: StationNumber,
    route: RouteName,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ArrivalsOut:
    stop = await _stop_info(session, station_number)
    if route not in {o.route for o in stop.occurrences}:
        raise HTTPException(status_code=404, detail="그 정류장에 서지 않는 노선이다.")
    try:
        result = await request.app.state.service.route_arrivals(stop, route)
    except ServiceUnavailable:
        raise _unavailable() from None
    return _out(station_number, result)


class StationOut(Camel):
    seq: int
    name: str
    turn: bool


class LineOut(Camel):
    """정류장에 서는 한 번(route, staOrder)마다 그 노선의 전체 정류소 순서다."""

    route: str
    sta_order: int
    stations: list[StationOut]


class LinesOut(Camel):
    lines: list[LineOut]


@router.get("/{station_number}/stations", response_model=LinesOut)
async def route_lines(
    station_number: StationNumber,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LinesOut:
    """노선도용 정류소 순서. 버스 위치는 staOrder - locationNo 로 구한다."""
    stop = await _stop_info(session, station_number)
    service = request.app.state.service
    try:
        lines = [
            LineOut(
                route=o.route,
                sta_order=o.sta_order,
                stations=[
                    StationOut(**s)
                    for s in await service.route_stations(o.gbis_route_id)
                ],
            )
            for o in stop.occurrences
        ]
    except ServiceUnavailable:
        raise _unavailable() from None
    return LinesOut(lines=lines)


@status_router.get("/status")
async def status(request: Request) -> dict:
    try:
        return await request.app.state.service.status()
    except Exception:  # noqa: BLE001  상태 조회가 Redis 오류로 500 을 내지 않게 한다.
        raise _unavailable() from None
