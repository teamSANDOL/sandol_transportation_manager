"""우선순위 1, 2위 정류장 10곳을 seed SQL 로 넣는다.

테이블은 app/models.py 가 먼저 만든다(app/init_db.py).
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None

SEED = Path(__file__).resolve().parent.parent / "seed_stops.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SEED.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DELETE FROM stop_routes")
    op.execute("DELETE FROM routes")
    op.execute("DELETE FROM stops")
