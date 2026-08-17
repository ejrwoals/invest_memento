"""계열 조회 — 카탈로그 검색(2단계 폼의 매핑 보조)과 스냅샷 구간(추이 차트용).

- 검색은 label·search_keywords 에 대한 단순 텍스트 매칭 — 최종 선택은 LLM 이 아니라
  사용자가 폼에서 확인한다 (05 §3.2). 카탈로그는 수십 행이라 메모리에서 거른다.
- 개별 주식은 "참조 시 자동 등록"(05 §3.1)이라 카탈로그에 없을 수 있다 — instruments
  까지 뒤지고, 둘 다 미스인 티커 모양 쿼리는 합성 후보(unregistered)로 돌려준다.
  검색에서 안 나오면 참조 자체가 불가능해 자동 등록 경로가 막히기 때문이다.
- 스냅샷은 전역 캐시라 user 스코핑이 없다 — 인증만 요구한다.
- ecos 코드는 '통계표코드/주기/항목코드'처럼 슬래시를 품으므로 path 컨버터를 쓴다.
"""

import re
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select

from app.auth import RequireUser
from app.db import SessionDep
from app.db.models import Instrument, SeriesCatalogEntry, SeriesSnapshot

router = APIRouter()

# 티커 모양 — 해외는 영문 1~6자(대문자화), 국내 주식은 숫자 6자리 (collect.is_kr_code 와 동일 구분)
_TICKER_RE = re.compile(r"^(?:[A-Za-z]{1,6}|\d{6})$")


class CatalogEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    code: str
    label: str
    kind: str
    unit: str | None
    has_intraday: bool
    unregistered: bool = False  # 카탈로그 미등록 — 선택하면 참조 시 자동 등록된다


class SnapshotOut(BaseModel):
    date: date
    close: Decimal
    high: Decimal | None
    low: Decimal | None


@router.get("/series/search")
async def search_series(
    user: RequireUser, session: SessionDep, q: Annotated[str, Query(min_length=1)]
) -> list[CatalogEntryOut]:
    entries = (await session.scalars(select(SeriesCatalogEntry))).all()
    stripped = q.strip()
    needle = stripped.lower()
    results: dict[tuple[str, str], CatalogEntryOut] = {}
    for e in entries:
        if needle in e.label.lower() or any(
            needle in k.lower() for k in (e.search_keywords or [])
        ):
            results[(e.provider, e.code)] = CatalogEntryOut.model_validate(e)

    # instruments 매칭 — 카탈로그 미등록 종목도 후보로 낸다 (선택 시 자동 등록 경로)
    pattern = f"%{stripped}%"
    instruments = (
        await session.scalars(
            select(Instrument).where(
                or_(Instrument.name.ilike(pattern), Instrument.symbol.ilike(pattern))
            )
        )
    ).all()
    entry_by_key = {(e.provider, e.code): e for e in entries}
    for inst in instruments:
        key = ("kis", inst.symbol)
        if key in results:
            continue
        entry = entry_by_key.get(key)
        if entry is not None:
            # 카탈로그에는 있는데 텍스트 매칭만 빗나간 경우 — 카탈로그 행이 정본이다
            results[key] = CatalogEntryOut.model_validate(entry)
        else:
            results[key] = CatalogEntryOut(
                provider="kis",
                code=inst.symbol,
                label=inst.name,
                kind="equity",
                unit=None,
                has_intraday=True,
                unregistered=True,
            )

    # 둘 다 미스인 티커 모양 쿼리 — 합성 후보 1건. instruments 에도 없는 새 종목(예: MCD)을
    # 2단계 폼에서 고를 수 있어야 참조 시 자동 등록이 성립한다.
    if not results and _TICKER_RE.fullmatch(stripped):
        code = stripped.upper()
        results[("kis", code)] = CatalogEntryOut(
            provider="kis",
            code=code,
            label=f"{code} (새 종목)",
            kind="equity",
            unit=None,
            has_intraday=True,
            unregistered=True,
        )
    return list(results.values())


@router.get("/series/{provider}/{code:path}")
async def get_series(
    provider: str,
    code: str,
    user: RequireUser,
    session: SessionDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> list[SnapshotOut]:
    known = await session.scalar(
        select(SeriesCatalogEntry.code).where(
            SeriesCatalogEntry.provider == provider, SeriesCatalogEntry.code == code
        )
    )
    if known is None:
        raise HTTPException(status_code=404, detail="series not found")
    query = (
        select(SeriesSnapshot)
        .where(SeriesSnapshot.provider == provider, SeriesSnapshot.code == code)
        .order_by(SeriesSnapshot.date)
    )
    if date_from is not None:
        query = query.where(SeriesSnapshot.date >= date_from)
    if date_to is not None:
        query = query.where(SeriesSnapshot.date <= date_to)
    snapshots = (await session.scalars(query)).all()
    return [
        SnapshotOut(date=s.date, close=s.close, high=s.high, low=s.low) for s in snapshots
    ]
