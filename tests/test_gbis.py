import httpx
import pytest

from app.gbis import GbisClient, GbisError
from tests.conftest import load_fixture

KEY = "secret-key-value"


def client_for(handler) -> GbisClient:
    return GbisClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def ok(body: dict, remaining: str = "4000") -> httpx.Response:
    return httpx.Response(
        200,
        json=body,
        headers={"x-ratelimit-limit": "10000", "x-ratelimit-remaining": remaining},
    )


async def test_list_returns_rows_and_quota_headers() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return ok(load_fixture("gbis_list_25173.json"), "4268")

    result = await client_for(handler).arrival_list("224000513", KEY)

    assert len(result.rows) == 19
    assert (result.limit, result.remaining) == (10000, 4268)
    assert seen == [{"stationId": "224000513", "serviceKey": KEY, "format": "json"}]


async def test_item_sends_route_and_order_and_returns_one_row() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return ok(load_fixture("gbis_item_25173_3400.json"))

    result = await client_for(handler).arrival_item("224000513", "224000050", 3, KEY)

    assert len(result.rows) == 1
    assert seen[0]["routeId"] == "224000050" and seen[0]["staOrder"] == "3"


async def test_route_stations_uses_route_service_and_returns_rows() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        return ok(load_fixture("gbis_route_stations_3400.json"))

    result = await client_for(handler).route_stations("224000050", KEY)

    assert len(result.rows) == 69
    path, params = seen[0]
    assert path.endswith("busrouteservice/v2/getBusRouteStationListv2")
    assert params["routeId"] == "224000050"


async def test_result_code_4_is_empty_not_error() -> None:
    body = {"response": {"msgHeader": {"resultCode": 4, "resultMessage": "없음"}}}

    result = await client_for(lambda r: ok(body)).arrival_list("1", KEY)

    assert result.rows == []


async def test_retries_once_on_timeout() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return ok(load_fixture("gbis_list_25779.json"))

    result = await client_for(handler).arrival_list("1", KEY)

    assert len(calls) == 2 and len(result.rows) == 3


async def test_gives_up_after_two_attempts() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(GbisError):
        await client_for(handler).arrival_list("1", KEY)

    assert len(calls) == 2


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<xml>error</xml>"),
        httpx.Response(401, text="bad key"),
        httpx.Response(
            200,
            json={"response": {"msgHeader": {"resultCode": 1, "resultMessage": "x"}}},
        ),
    ],
)
async def test_errors_never_contain_the_key(response: httpx.Response) -> None:
    with pytest.raises(GbisError) as error:
        await client_for(lambda r: response).arrival_list("1", KEY)

    assert KEY not in str(error.value)
