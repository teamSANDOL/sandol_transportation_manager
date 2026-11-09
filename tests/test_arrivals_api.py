import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import load_fixture, make_http, make_redis

STATION_IDS = {"224000513": "25173", "224000835": "25835"}


class Gbis:
    """GBIS 를 흉내 내는 MockTransport 핸들러. 부른 횟수를 센다."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path.endswith("getBusRouteStationListv2"):
            body = load_fixture("gbis_route_stations_3400.json")
        elif request.url.path.endswith("getBusArrivalItemv2"):
            body = load_fixture("gbis_item_25173_3400.json")
        else:
            body = load_fixture("gbis_list_25173.json")
        return httpx.Response(
            200,
            json=body,
            headers={"x-ratelimit-limit": "10000", "x-ratelimit-remaining": "9000"},
        )


@pytest.fixture
def gbis() -> Gbis:
    return Gbis()


def build(migrated_db, monkeypatch, gbis, *, keys=("test-key",), limit="1000"):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("BUS_RATE_LIMIT_PER_MINUTE", limit)
    for n in range(1, 5):
        monkeypatch.delenv(f"DATA_GO_KR_KEY{n}", raising=False)
    for n, key in enumerate(keys, start=1):
        monkeypatch.setenv(f"DATA_GO_KR_KEY{n}", key)
    redis, http = make_redis(), make_http(gbis)
    return TestClient(create_app(redis_client=redis, http_client=http))


@pytest.fixture
def client(migrated_db, monkeypatch, gbis):
    with build(migrated_db, monkeypatch, gbis) as client:
        yield client


def test_arrivals_returns_only_commute_routes_and_normalized_names(
    client, gbis
) -> None:
    body = client.get("/stops/25173/arrivals").json()

    routes = {a["route"] for a in body["arrivals"]}
    assert body["source"] == "live" and body["stale"] is False
    assert {"3400", "P9540", "11-B"} <= routes
    assert not routes & {"22", "23", "55", "99", "123", "125"}
    assert len(gbis.calls) == 1


def test_second_request_comes_from_cache_without_gbis_call(client, gbis) -> None:
    client.get("/stops/25173/arrivals")

    body = client.get("/stops/25173/arrivals").json()

    assert body["source"] == "cache" and len(gbis.calls) == 1


def test_route_query_filters_without_extra_call(client, gbis) -> None:
    body = client.get("/stops/25173/arrivals", params={"route": "3400"}).json()

    assert {a["route"] for a in body["arrivals"]} == {"3400"}
    assert len(gbis.calls) == 1


def test_gbis_is_never_called_for_unknown_stop_or_route(client, gbis) -> None:
    assert client.get("/stops/99999/arrivals").status_code == 404
    assert (
        client.get("/stops/25173/arrivals", params={"route": "999"}).status_code == 404
    )
    assert client.get("/stops/25173/routes/999/arrival").status_code == 404

    assert gbis.calls == []


@pytest.mark.parametrize("path", ["/stops/abc/arrivals", "/stops/2517/arrivals"])
def test_malformed_station_is_422_without_gbis_call(client, gbis, path) -> None:
    assert client.get(path).status_code == 422
    assert gbis.calls == []


def test_malformed_route_is_422_without_gbis_call(client, gbis) -> None:
    assert (
        client.get("/stops/25173/arrivals", params={"route": "a b;"}).status_code == 422
    )
    assert client.get("/stops/25173/routes/%3Cx%3E/arrival").status_code == 422
    assert gbis.calls == []


def test_route_arrival_uses_item_api(client, gbis) -> None:
    body = client.get("/stops/25173/routes/3400/arrival").json()

    assert {a["route"] for a in body["arrivals"]} == {"3400"}
    assert [c.url.path.rsplit("/", 1)[-1] for c in gbis.calls] == [
        "getBusArrivalItemv2"
    ] * len(gbis.calls)
    assert all(c.url.params["routeId"] for c in gbis.calls)


def test_rate_limit_stops_requests_before_they_reach_gbis(
    migrated_db, monkeypatch, gbis
) -> None:
    with build(migrated_db, monkeypatch, gbis, limit="3") as client:
        codes = [client.get("/stops/25173/arrivals").status_code for _ in range(5)]
        other = client.get("/stops", headers={"X-Real-IP": "9.9.9.9"}).status_code
        limited = client.get("/stops/25173/arrivals")

    assert codes == [200, 200, 200, 429, 429]
    assert other == 200
    assert limited.headers["Retry-After"]
    assert len(gbis.calls) == 1


def test_health_is_not_rate_limited(migrated_db, monkeypatch, gbis) -> None:
    with build(migrated_db, monkeypatch, gbis, limit="1") as client:
        codes = [client.get("/health").status_code for _ in range(5)]

    assert codes == [200] * 5


def test_without_keys_returns_503_and_never_calls_gbis(
    migrated_db, monkeypatch, gbis
) -> None:
    with build(migrated_db, monkeypatch, gbis, keys=()) as client:
        response = client.get("/stops/25173/arrivals")

    assert response.status_code == 503 and gbis.calls == []


def test_status_shows_quota_without_key_values(migrated_db, monkeypatch, gbis) -> None:
    monkeypatch.setenv("DEV_FRONTEND_MODE", "1")
    with build(migrated_db, monkeypatch, gbis) as client:
        _status_checks(client)


def test_status_is_not_registered_when_frontend_mode_is_off(
    client,
) -> None:
    assert client.get("/status").status_code == 404


def _status_checks(client) -> None:
    client.get("/stops/25173/arrivals")

    body = client.get("/status").json()

    assert body["fetchMode"] == "on_demand" and body["keysConfigured"] == 1
    assert body["quota"]["list"] == [{"keyNo": 1, "remaining": 9000}]
    assert "test-key" not in client.get("/status").text


def test_stations_returns_ordered_line_per_occurrence(client, gbis) -> None:
    lines = client.get("/stops/25173/stations").json()["lines"]

    line = next(x for x in lines if x["route"] == "3400")
    seqs = [s["seq"] for s in line["stations"]]
    assert seqs == sorted(seqs) and len(seqs) == 69
    assert line["staOrder"] == 3
    assert line["stations"][2]["name"] == "이마트"
    assert sum(s["turn"] for s in line["stations"]) == 1


def test_stations_are_cached_for_a_day(client, gbis) -> None:
    client.get("/stops/25173/stations")
    calls = len(gbis.calls)
    client.get("/stops/25173/stations")

    assert len(gbis.calls) == calls


def test_stations_404_for_unknown_stop(client, gbis) -> None:
    assert client.get("/stops/99999/stations").status_code == 404
    assert gbis.calls == []
