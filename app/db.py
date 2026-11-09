"""비동기 DB 세션."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import database_url


def create_session_factory() -> async_sessionmaker[AsyncSession]:
    """엔진은 첫 요청 때 연결한다. 앱을 만들 때는 DB 가 없어도 된다."""
    return async_sessionmaker(
        create_async_engine(database_url()), expire_on_commit=False
    )


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session
