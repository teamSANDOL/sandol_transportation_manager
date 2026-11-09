import json
import os
from pathlib import Path

import fakeredis
import httpx
import pytest
from sqlalchemy import create_engine, text

from app.init_db import init_db
from app.models import Base


@pytest.fixture(scope="session")
def db_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 이 없어 DB 테스트를 건너뛴다")
    return url


@pytest.fixture(scope="session")
def migrated_db(db_url: str):
    """테스트 DB 를 비우고 테이블 생성과 seed 를 시작 때처럼 적용한다."""
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    engine = create_engine(db_url)
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()
    init_db()
    yield db_url
    if previous is None:
        del os.environ["DATABASE_URL"]
    else:
        os.environ["DATABASE_URL"] = previous


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """테스트가 실제 GBIS 로 나가는 일이 없도록 진짜 전송 계층을 막는다."""

    def blocked(*args, **kwargs):
        raise AssertionError(
            "테스트에서 실제 HTTP 호출은 금지다. MockTransport 를 쓴다."
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


def make_redis() -> fakeredis.FakeAsyncRedis:
    server = fakeredis.FakeServer()
    return fakeredis.FakeAsyncRedis(server=server, decode_responses=True)


def make_http(handler=None) -> httpx.AsyncClient:
    """MockTransport 로만 나가는 클라이언트. 기본 핸들러는 500 이다."""
    handler = handler or (lambda request: httpx.Response(500))
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
