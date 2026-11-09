"""정류장 목록과 상세 API."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import Stop, StopRoute

router = APIRouter(prefix="/stops")

StationNumber = Annotated[str, Path(pattern=r"^\d{5}$")]


class Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class StopSummary(Camel):
    station_number: str
    name: str
    priority: int
    polled: bool
    directions: list[str]
    routes: list[str]


class RouteInStop(Camel):
    name: str
    direction: str
    via: str | None


class StopDetail(StopSummary):
    station_id: str
    route_details: list[RouteInStop]


class StopList(Camel):
    stops: list[StopSummary]


def _directions(links: list[StopRoute]) -> list[str]:
    found = {
        d
        for link in links
        for d in (
            ("commute", "return") if link.direction == "both" else (link.direction,)
        )
    }
    return sorted(found, key=("commute", "return").index)


def summarize(stop: Stop) -> StopSummary:
    links = stop.stop_routes
    return StopSummary(
        station_number=stop.station_number,
        name=stop.name,
        priority=stop.priority,
        polled=stop.polled,
        directions=_directions(links),
        routes=list(dict.fromkeys(link.route.name for link in links)),
    )


LOAD_ROUTES = selectinload(Stop.stop_routes).selectinload(StopRoute.route)


async def find_stop(session: AsyncSession, station_number: str) -> Stop | None:
    return await session.scalar(
        select(Stop).options(LOAD_ROUTES).where(Stop.station_number == station_number)
    )


@router.get("", response_model=StopList)
async def list_stops(
    session: Annotated[AsyncSession, Depends(get_session)],
    direction: Literal["commute", "return"] | None = None,
    priority: Annotated[int | None, Query(ge=1, le=2)] = None,
) -> StopList:
    query = select(Stop).options(LOAD_ROUTES).order_by(Stop.priority, Stop.id)
    if priority is not None:
        query = query.where(Stop.priority == priority)
    stops = [summarize(s) for s in (await session.scalars(query)).all()]
    if direction is not None:
        stops = [s for s in stops if direction in s.directions]
    return StopList(stops=stops)


@router.get("/{station_number}", response_model=StopDetail)
async def get_stop(
    station_number: StationNumber,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StopDetail:
    stop = await find_stop(session, station_number)
    if stop is None:
        raise HTTPException(status_code=404, detail="없는 정류소 번호다.")
    return StopDetail(
        **summarize(stop).model_dump(),
        station_id=stop.station_id,
        route_details=[
            RouteInStop(name=name, direction=direction, via=via)
            for name, direction, via in dict.fromkeys(
                (link.route.name, link.direction, link.route.via)
                for link in stop.stop_routes
            )
        ],
    )
