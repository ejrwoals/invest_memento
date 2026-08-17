"""공용 픽스처 — series 테스트가 쓰는 sqlite(aiosqlite) 파일 DB.

패턴은 test_conversations 와 같다: NullPool 로 이벤트 루프 간 커넥션 재사용을 막고,
스키마는 ORM 매핑에서 create_all 로 만든다 (CHECK·트리거·RLS 는 DB 통합 테스트의 몫).
"""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import Base


@pytest.fixture()
def series_db(tmp_path: Path) -> Iterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/series.db", poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create())
    yield maker
