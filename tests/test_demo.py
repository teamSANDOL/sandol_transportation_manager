import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_http, make_redis


@pytest.fixture
def client(migrated_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("DEV_FRONTEND_MODE", "1")
    redis, http = make_redis(), make_http()
    with TestClient(create_app(redis_client=redis, http_client=http)) as client:
        yield client


def test_sidebar_lists_every_stop_and_nothing_is_selected(client) -> None:
    html = client.get("/demo").text

    assert html.count('class="side-item') == 12
    assert "/demo/stops/25173" in html and "이마트" in html
    assert 'data-station=""' in html
    assert "왼쪽에서 정류장을 선택한다" in html
    assert 'id="quota"' in html


def test_selected_stop_is_marked_active_with_kiosk_script(client) -> None:
    html = client.get("/demo/stops/25173").text

    assert '<div id="app"' in html and 'data-station="25173"' in html
    assert html.count("side-item active") == 1
    assert "/static/js/kiosk.js?v=" in html


def test_unknown_or_malformed_station_is_rejected(client) -> None:
    assert client.get("/demo/stops/99999").status_code == 404
    assert client.get("/demo/stops/abc").status_code == 422


def test_static_assets_are_served(client) -> None:
    assert client.get("/static/css/kiosk.css").status_code == 200
    assert client.get("/static/js/kiosk.js").status_code == 200


def test_pages_never_reach_gbis(client) -> None:
    client.get("/demo")
    client.get("/demo/stops/25173")
    # 페이지는 GBIS 를 부르지 않는다. 호출이 있었다면 MockTransport 의 500 이 보인다.
    assert client.get("/stops").status_code == 200


def test_frontend_is_not_loaded_when_mode_is_off(migrated_db, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("DEV_FRONTEND_MODE", "0")
    app = create_app(redis_client=make_redis(), http_client=make_http())
    with TestClient(app) as off:
        assert off.get("/demo").status_code == 404
        assert off.get("/demo/stops/25173").status_code == 404
        assert off.get("/static/css/kiosk.css").status_code == 404
        assert off.get("/health").status_code == 200
        assert off.get("/stops").status_code == 200
