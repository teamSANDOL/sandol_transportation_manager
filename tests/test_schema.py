from sqlalchemy import create_engine, inspect

from app.models import Base

TABLES = {
    "stops",
    "routes",
    "stop_routes",
}


def test_startup_creates_every_model_table(migrated_db: str) -> None:
    engine = create_engine(migrated_db)

    assert set(Base.metadata.tables) == TABLES
    assert TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()
