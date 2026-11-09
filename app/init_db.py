"""컨테이너 시작 때 테이블을 만들고 seed 를 넣는다."""

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from app.config import database_url
from app.models import Base


def init_db() -> None:
    """테이블은 models 로 만들고 정류장과 노선 seed 만 migration 으로 넣는다.

    create_all 은 없는 테이블만 만든다. 데이터가 쌓인 뒤 구조를 바꾸려면 구조
    migration 이 필요하다.
    """
    engine = create_engine(database_url())
    Base.metadata.create_all(engine)
    engine.dispose()
    command.upgrade(Config("alembic.ini"), "head")


if __name__ == "__main__":
    init_db()
