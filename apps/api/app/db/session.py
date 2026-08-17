"""비동기 DB 엔진·세션.

접속은 Supabase Supavisor **transaction pooler**를 지난다. transaction 모드에서는
서버측 prepared statement가 커넥션 간에 공유되지 않아 이름 충돌이 나므로:
- `poolclass=NullPool` — 풀링은 Supavisor가 한다. 앱에서 이중 풀링하지 않는다.
- asyncpg `statement_cache_size=0` + 방언 `prepared_statement_cache_size=0`
- statement 이름을 uuid로 매번 새로 만들어 재사용 자체를 없앤다.
근거: docs/dev/02-backend.md §1(스택), SQLAlchemy asyncpg 방언의 pgbouncer 가이드.
"""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated
from uuid import uuid4

from fastapi import Depends
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings


def _asyncpg_url(url: str) -> URL:
    """Supabase가 주는 postgresql:// 문자열을 asyncpg 방언 URL로 바꾼다.

    asyncpg는 libpq의 `sslmode` 쿼리 파라미터를 모르므로 떼어낸다
    (Supabase pooler는 어차피 기본 협상으로 TLS가 붙는다).
    문자열로 되돌리지 않고 URL 객체를 그대로 쓴다 — str(URL)은 비밀번호를 가린다.
    """
    parsed = make_url(url).set(drivername="postgresql+asyncpg")
    query = {k: v for k, v in parsed.query.items() if k not in ("sslmode", "pgbouncer")}
    # 방언 파라미터 — SQLAlchemy 쪽 prepared statement 캐시도 끈다
    query["prepared_statement_cache_size"] = "0"
    return parsed.set(query=query)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    return create_async_engine(
        _asyncpg_url(settings.database_url),
        poolclass=NullPool,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
        },
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
