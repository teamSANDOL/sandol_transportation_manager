FROM ghcr.io/astral-sh/uv:0.12.20 AS uv

FROM python:3.11-slim-bookworm AS builder

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.11-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app alembic.ini /app/alembic.ini
COPY --chown=app:app migrations/ /app/migrations/
COPY --chown=app:app app/ /app/app/

USER app:app
EXPOSE 8000
CMD ["sh", "-c", "python -m app.init_db && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000"]
