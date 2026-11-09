"""GBIS 응답 행을 통학 노선만 같은 형태로 만든다."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Bus:
    predict_sec: int | None
    predict_minutes: int | None
    plate_no: str | None
    location_no: int | None
    remain_seat_cnt: int | None
    crowded: int | None
    low_plate: int | None


@dataclass(frozen=True)
class Arrival:
    route: str
    dest_name: str
    sta_order: int
    state: str
    buses: tuple[Bus, ...]


def route_name(raw: Any) -> str:
    """P9540(출근) 같은 이름에서 괄호 꼬리를 떼고 항상 문자열로 만든다."""
    return re.sub(r"\(.*\)$", "", str(raw)).strip()


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _bus(row: Mapping[str, Any], slot: int) -> Bus | None:
    bus = Bus(
        predict_sec=_int(row.get(f"predictTimeSec{slot}")),
        predict_minutes=_int(row.get(f"predictTime{slot}")),
        plate_no=_text(row.get(f"plateNo{slot}")),
        location_no=_int(row.get(f"locationNo{slot}")),
        remain_seat_cnt=_int(row.get(f"remainSeatCnt{slot}")),
        crowded=_int(row.get(f"crowded{slot}")),
        low_plate=_int(row.get(f"lowPlate{slot}")),
    )
    has_data = (bus.predict_sec, bus.predict_minutes, bus.plate_no) != (None,) * 3
    return bus if has_data else None


def _state(row: Mapping[str, Any], buses: tuple[Bus, ...]) -> str:
    if buses and (buses[0].predict_sec, buses[0].predict_minutes) != (None, None):
        return "running"
    return "wait" if row.get("flag") == "WAIT" else "none"


def normalize(
    rows: list[dict[str, Any]], route_names: Mapping[str, str]
) -> list[Arrival]:
    """route_names(GBIS routeId -> 노선 이름)에 없는 노선의 행은 버린다."""
    arrivals = []
    for row in rows:
        name = route_names.get(str(row.get("routeId")))
        if name is None:
            continue
        buses = tuple(b for slot in (1, 2) if (b := _bus(row, slot)) is not None)
        arrivals.append(
            Arrival(
                route=name,
                dest_name=str(row.get("routeDestName", "")),
                sta_order=int(row.get("staOrder", 0)),
                state=_state(row, buses),
                buses=buses,
            )
        )
    return arrivals
