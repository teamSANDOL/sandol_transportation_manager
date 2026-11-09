"""GBIS 버스도착정보 호출. 이 서비스에서 GBIS 로 나가는 유일한 경로다."""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

# httpx 의 INFO 로그는 serviceKey 가 든 요청 URL 을 그대로 남긴다.
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE_URL = "https://apis.data.go.kr/6410000/busarrivalservice/v2"
ROUTE_BASE_URL = "https://apis.data.go.kr/6410000/busrouteservice/v2"
TIMEOUT = httpx.Timeout(10.0)
# 재시도도 quota 를 쓸 수 있어서 한 번만 더 시도한다.
ATTEMPTS = 2


class GbisError(Exception):
    """GBIS 호출 실패. 메시지에 키나 URL 을 넣지 않는다."""


@dataclass(frozen=True)
class GbisResponse:
    rows: list[dict[str, Any]]
    limit: int | None
    remaining: int | None


def _header_int(headers: httpx.Headers, name: str) -> int | None:
    value = headers.get(name)
    return int(value) if value is not None and value.isdigit() else None


def _as_rows(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class GbisClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def arrival_list(self, station_id: str, key: str) -> GbisResponse:
        """정류소에 서는 모든 노선의 도착 예정(getBusArrivalListv2)."""
        return await self._get(
            "getBusArrivalListv2", {"stationId": station_id}, key, "busArrivalList"
        )

    async def arrival_item(
        self, station_id: str, route_id: str, sta_order: int, key: str
    ) -> GbisResponse:
        """노선 하나의 도착 예정(getBusArrivalItemv2). 목록조회와 quota 가 따로다."""
        params = {
            "stationId": station_id,
            "routeId": route_id,
            "staOrder": str(sta_order),
        }
        return await self._get("getBusArrivalItemv2", params, key, "busArrivalItem")

    async def route_stations(self, route_id: str, key: str) -> GbisResponse:
        """노선이 서는 정류소 순서(getBusRouteStationListv2). quota 가 따로다."""
        return await self._get(
            "getBusRouteStationListv2",
            {"routeId": route_id},
            key,
            "busRouteStationList",
            ROUTE_BASE_URL,
        )

    async def _get(
        self,
        path: str,
        params: dict[str, str],
        key: str,
        body_key: str,
        base_url: str = BASE_URL,
    ) -> GbisResponse:
        query = {**params, "serviceKey": key, "format": "json"}
        reason = "unknown"
        for _ in range(ATTEMPTS):
            try:
                response = await self._http.get(
                    f"{base_url}/{path}", params=query, timeout=TIMEOUT
                )
            except httpx.TimeoutException:
                reason = "timeout"
                continue
            except httpx.TransportError:
                reason = "connection"
                continue
            if response.status_code >= 500:
                reason = f"http {response.status_code}"
                continue
            return self._parse(response, body_key)
        raise GbisError(reason)

    @staticmethod
    def _parse(response: httpx.Response, body_key: str) -> GbisResponse:
        if response.status_code != 200:
            raise GbisError(f"http {response.status_code}")
        try:
            body = response.json()["response"]
            code = body["msgHeader"]["resultCode"]
        except (ValueError, KeyError, TypeError):
            raise GbisError("unexpected body") from None
        # resultCode 4 는 결과 없음이다. 오류가 아니라 빈 목록으로 본다.
        if code not in (0, 4):
            raise GbisError(f"resultCode {code}")
        rows = _as_rows((body.get("msgBody") or {}).get(body_key))
        return GbisResponse(
            rows=rows,
            limit=_header_int(response.headers, "x-ratelimit-limit"),
            remaining=_header_int(response.headers, "x-ratelimit-remaining"),
        )
