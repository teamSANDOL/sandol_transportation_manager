from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

SEED = Path("migrations/seed_stops.sql")


@pytest.fixture
def engine(migrated_db: str):
    engine = create_engine(migrated_db)
    yield engine
    engine.dispose()


def _count(engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def test_seed_has_twelve_stops_and_six_polled(engine) -> None:
    with engine.connect() as conn:
        polled = conn.execute(
            text("SELECT count(*) FROM stops WHERE polled")
        ).scalar_one()

    assert _count(engine, "stops") == 12
    assert polled == 6


def test_seed_runs_twice_without_duplicates(engine) -> None:
    before = [_count(engine, t) for t in ("stops", "routes", "stop_routes")]

    with engine.begin() as conn:
        conn.exec_driver_sql(SEED.read_text(encoding="utf-8"))

    assert [_count(engine, t) for t in ("stops", "routes", "stop_routes")] == before
