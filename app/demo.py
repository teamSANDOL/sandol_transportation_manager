"""테스트용 프론트. 정류장 목록과 키오스크 형태의 도착 정보 화면이다."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Stop
from app.stops import LOAD_ROUTES, StationNumber, find_stop, summarize

WEB_DIR = Path(__file__).parent / "web"
templates = Jinja2Templates(directory=WEB_DIR / "templates")
# 정적 파일이 바뀌면 주소도 바뀌게 해서 브라우저가 이전 JS 와 CSS 를 쓰지 않게 한다.
ASSET_VERSION = int(
    max(f.stat().st_mtime for f in (WEB_DIR / "static").rglob("*") if f.is_file())
)
templates.env.globals["asset_version"] = ASSET_VERSION
router = APIRouter(prefix="/demo", include_in_schema=False)

DIRECTION_LABELS = {"commute": "등교", "return": "하교"}
# 도착 정보를 다시 받는 주기(초). rate limit(분당 30회) 안에서 여유를 둔 값이다.
REFRESH_SECONDS = 15


async def _page(
    request: Request, session: AsyncSession, selected: Stop | None
) -> HTMLResponse:
    stops = await session.scalars(
        select(Stop).options(LOAD_ROUTES).order_by(Stop.priority, Stop.id)
    )
    return templates.TemplateResponse(
        request,
        "demo.html",
        {
            "stops": [summarize(s) for s in stops.all()],
            "selected": summarize(selected) if selected else None,
            "labels": DIRECTION_LABELS,
            "refresh_seconds": REFRESH_SECONDS,
        },
    )


@router.get("", response_class=HTMLResponse)
async def stop_list(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    return await _page(request, session, None)


@router.get("/stops/{station_number}", response_class=HTMLResponse)
async def kiosk(
    station_number: StationNumber,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    stop = await find_stop(session, station_number)
    if stop is None:
        raise HTTPException(status_code=404, detail="없는 정류소 번호다.")
    return await _page(request, session, stop)
