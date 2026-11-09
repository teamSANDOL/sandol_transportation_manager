import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_http, make_redis


@pytest.fixture
def client(migrated_db: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    redis, http = make_redis(), make_http()
    with TestClient(create_app(redis_client=redis, http_client=http)) as client:
        yield client


def test_list_returns_twelve_stops_ordered_by_priority(client: TestClient) -> None:
    stops = client.get("/stops").json()["stops"]

    assert len(stops) == 12
    assert [s["priority"] for s in stops] == [1, 1, 1, 1] + [2] * 8
    first = stops[0]
    assert first["stationNumber"] == "25173"
    assert first["polled"] is True


def test_priority_filter(client: TestClient) -> None:
    stops = client.get("/stops", params={"priority": 1}).json()["stops"]

    assert {s["stationNumber"] for s in stops} == {"25173", "25174", "25854", "25680"}


def test_return_direction_excludes_commute_only_stops(client: TestClient) -> None:
    numbers = {
        s["stationNumber"]
        for s in client.get("/stops", params={"direction": "return"}).json()["stops"]
    }

    commute_only = {"25835", "25198", "22851", "25484"}
    assert not numbers & commute_only
    assert "25173" in numbers


def test_detail_has_station_id_and_routes(client: TestClient) -> None:
    body = client.get("/stops/25173").json()

    assert body["stationId"] == "224000513"
    route = next(r for r in body["routeDetails"] if r["name"] == "3400")
    assert route["direction"] == "return"


def test_unknown_station_is_404(client: TestClient) -> None:
    assert client.get("/stops/99999").status_code == 404


@pytest.mark.parametrize("value", ["abc", "2517", "251733"])
def test_malformed_station_number_is_422(client: TestClient, value: str) -> None:
    assert client.get(f"/stops/{value}").status_code == 422


def test_bad_query_is_422(client: TestClient) -> None:
    assert client.get("/stops", params={"priority": 3}).status_code == 422
    assert client.get("/stops", params={"direction": "x"}).status_code == 422


def test_detail_has_via_for_routes_listed_in_the_overview(client: TestClient) -> None:
    routes = {
        r["name"]: r["via"] for r in client.get("/stops/25173").json()["routeDetails"]
    }

    assert routes["3400"] == "배곧중심상가, 사당역3번출구"
    assert routes["25"] is None


def test_new_commute_stops_from_the_route_station_lists(client: TestClient) -> None:
    sadang = client.get("/stops/22851").json()
    city = client.get("/stops/25484").json()

    assert sadang["name"] == "사당역14번출구" and sadang["stationId"] == "121001325"
    assert sorted(sadang["routes"]) == ["3400", "6502", "P9541"]
    assert city["name"] == "시흥시청역" and city["stationId"] == "224000586"
    assert sorted(city["routes"]) == ["33", "3401", "5602"]
    assert sadang["directions"] == city["directions"] == ["commute"]
    assert sadang["polled"] is False and city["polled"] is False
