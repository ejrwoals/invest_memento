"""수집 배치 — 대상 산출·증분·백필·멱등 upsert·계열 단위 격리 (05 §4·§8).

외부 provider 는 모의 구현으로 대체한다 — 요청 구간을 기록해 증분 산출을 검증한다.
"""

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.series.collect as collect_module
from app.db.models import Galae, Note, Scenario, SeriesSnapshot, Watch
from app.series.collect import BACKFILL_MARGIN_DAYS, collect, collect_targets, is_kr_code
from app.series.providers import DailyBar

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
TODAY = date.today()


class FakeProvider:
    """요청 구간을 기록하고 코드별 고정 봉을 돌려주는 모의 provider.

    clip=False 면 요청 구간 밖 봉도 그대로 돌려준다 — 월·분기 계열처럼 기간 대표
    날짜가 겹쳐 오는 provider 를 흉내낸다 (호출자는 받은 것을 그대로 upsert 한다).
    """

    def __init__(self, name: str, bars: dict[str, list[DailyBar]], clip: bool = True) -> None:
        self.name = name
        self.bars = bars
        self.clip = clip
        self.calls: list[tuple[str, date, date]] = []

    def fetch_daily(self, code: str, start: date, end: date) -> list[DailyBar]:
        self.calls.append((code, start, end))
        bars = self.bars.get(code, [])
        if self.clip:
            return [b for b in bars if start <= b["date"] <= end]
        return list(bars)


class ExplodingProvider:
    name = "kis"

    def fetch_daily(self, code: str, start: date, end: date) -> list[DailyBar]:
        raise RuntimeError("boom")


def _bar(d: date, close: str) -> DailyBar:
    return DailyBar(date=d, close=D(close), high=D(close), low=D(close))


def _dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 9, 0)


def _note() -> Note:
    return Note(
        user_id=USER_ID,
        target_type="ticker",
        target_name="삼성전자",
        thesis_summary="테스트",
        color="#2563eb",
    )


def _auto(provider: str, code: str, created: date) -> Scenario:
    return Scenario(
        name="조건",
        resolution_type="auto",
        series_provider=provider,
        series_code=code,
        comparator="gte",
        target_value=D("110"),
        created_at=_dt(created),
    )


def test_is_kr_code_heuristic() -> None:
    assert is_kr_code("005930") and is_kr_code("0001")
    assert not is_kr_code("AAPL") and not is_kr_code("SPX")


def test_collect_targets_union_and_earliest_reference(
    series_db: async_sessionmaker[AsyncSession],
) -> None:
    async def _run() -> None:
        async with series_db() as s:
            note = _note()
            open_galae = Galae(question="q1", judge_end=TODAY + timedelta(days=30))
            open_galae.scenarios = [
                _auto("kis", "005930", TODAY - timedelta(days=5)),
                Scenario(name="manual", resolution_type="manual"),  # auto 아님 — 제외
            ]
            judged = Galae(question="q2", status="judged")
            judged.scenarios = [_auto("kis", "035720", TODAY - timedelta(days=9))]  # 갈래 닫힘
            note.galae = [open_galae, judged]
            note.watches = [
                Watch(
                    provider="fred",
                    code="DFF",
                    label="미국 기준금리",
                    created_at=_dt(TODAY - timedelta(days=20)),
                ),
                # 시나리오와 같은 계열을 더 이른 날짜에 참조 — 최소 기록일이 이긴다
                Watch(
                    provider="kis",
                    code="005930",
                    label="삼성전자",
                    created_at=_dt(TODAY - timedelta(days=15)),
                ),
            ]
            s.add(note)
            await s.commit()

        async with series_db() as s:
            targets = await collect_targets(s)
        assert {(t.provider, t.code) for t in targets} == {("kis", "005930"), ("fred", "DFF")}
        by_key = {(t.provider, t.code): t for t in targets}
        assert by_key[("kis", "005930")].first_referenced == TODAY - timedelta(days=15)
        assert by_key[("fred", "DFF")].first_referenced == TODAY - timedelta(days=20)

    asyncio.run(_run())


def test_backfill_then_incremental_from_last_plus_one(
    series_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    first_ref = TODAY - timedelta(days=10)
    fake = FakeProvider(
        "kis",
        {
            "005930": [
                _bar(TODAY - timedelta(days=3), "100"),
                _bar(TODAY - timedelta(days=2), "101"),
            ]
        },
    )
    monkeypatch.setattr(collect_module, "build_providers", lambda: {"kis": fake})

    async def _run() -> None:
        async with series_db() as s:
            note = _note()
            galae = Galae(question="q", judge_end=TODAY + timedelta(days=30))
            galae.scenarios = [_auto("kis", "005930", first_ref)]
            note.galae = [galae]
            s.add(note)
            await s.commit()

        # 최초: 백필 시작점 = 참조 최소 기록일 − 30일 (§4.3)
        async with series_db() as s:
            stats = await collect(s, providers_filter={"kis"}, market="kr")
        assert (stats.succeeded, stats.rows) == (1, 2)
        assert fake.calls[-1] == (
            "005930",
            first_ref - timedelta(days=BACKFILL_MARGIN_DAYS),
            TODAY,
        )

        # 증분: last + 1일부터 — 어제 배치가 죽어도 이 경로가 구멍을 메운다
        async with series_db() as s:
            await collect(s, providers_filter={"kis"}, market="kr")
        assert fake.calls[-1] == ("005930", TODAY - timedelta(days=1), TODAY)

    asyncio.run(_run())


def test_upsert_is_idempotent_and_overwrites_revisions(
    series_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    d = TODAY - timedelta(days=1)
    # clip=False — 증분 구간 밖(이미 저장된 날짜)의 수정치가 겹쳐 온다
    fake = FakeProvider("fred", {"DFF": [_bar(d, "5.25")]}, clip=False)
    monkeypatch.setattr(collect_module, "build_providers", lambda: {"fred": fake})

    async def _run() -> None:
        async with series_db() as s:
            note = _note()
            note.watches = [Watch(provider="fred", code="DFF", label="미국 기준금리")]
            s.add(note)
            # 같은 날짜에 이전 값이 이미 있다 — 수정치 발표 시나리오
            s.add(SeriesSnapshot(provider="fred", code="DFF", date=d, close=D("5.00")))
            await s.commit()

        async with series_db() as s:
            stats = await collect(s, providers_filter={"fred"})
        assert stats.failed == 0

        async with series_db() as s:
            rows = (
                await s.scalars(select(SeriesSnapshot).where(SeriesSnapshot.code == "DFF"))
            ).all()
        assert len(rows) == 1  # 중복 행이 아니라 덮어쓰기
        assert rows[0].close == D("5.25")

    asyncio.run(_run())


def test_series_level_failure_isolation(
    series_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    d = TODAY - timedelta(days=1)
    fake = FakeProvider("fred", {"DFF": [_bar(d, "5.25")]})
    monkeypatch.setattr(
        collect_module,
        "build_providers",
        lambda: {"fred": fake, "kis": ExplodingProvider()},
    )

    async def _run() -> None:
        async with series_db() as s:
            note = _note()
            note.watches = [
                Watch(provider="fred", code="DFF", label="미국 기준금리"),
                Watch(provider="kis", code="005930", label="삼성전자"),
            ]
            s.add(note)
            await s.commit()

        async with series_db() as s:
            stats = await collect(s, providers_filter={"fred", "kis"})
        # 실패한 계열만 건너뛰고 나머지는 진행한다 (§8)
        assert (stats.targets, stats.succeeded, stats.failed) == (2, 1, 1)
        async with series_db() as s:
            assert (
                await s.scalar(select(SeriesSnapshot.close).where(SeriesSnapshot.code == "DFF"))
            ) == D("5.25")

    asyncio.run(_run())


def test_market_filter_splits_kis_targets(
    series_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeProvider("kis", {})
    monkeypatch.setattr(collect_module, "build_providers", lambda: {"kis": fake})

    async def _run() -> None:
        async with series_db() as s:
            note = _note()
            note.watches = [
                Watch(provider="kis", code="005930", label="삼성전자"),
                Watch(provider="kis", code="AAPL", label="애플"),
            ]
            s.add(note)
            await s.commit()

        async with series_db() as s:
            await collect(s, providers_filter={"kis"}, market="kr")
        assert [c[0] for c in fake.calls] == ["005930"]

        async with series_db() as s:
            await collect(s, providers_filter={"kis"}, market="us")
        assert [c[0] for c in fake.calls[1:]] == ["AAPL"]

    asyncio.run(_run())


def test_unavailable_provider_is_skipped(
    series_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 키 없는 provider 는 레지스트리에 없다 — 해당 계열만 스킵하고 로그만 남긴다
    monkeypatch.setattr(collect_module, "build_providers", lambda: {})

    async def _run() -> None:
        async with series_db() as s:
            note = _note()
            note.watches = [Watch(provider="ecos", code="722Y001/D/0101000", label="기준금리")]
            s.add(note)
            await s.commit()

        async with series_db() as s:
            stats = await collect(s, providers_filter={"fred", "ecos"})
        assert (stats.targets, stats.succeeded, stats.failed) == (1, 0, 1)

    asyncio.run(_run())
