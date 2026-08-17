"""auto 평가 — comparator 4종·터치 규칙·progress·단조성 (05 §5).

순수 함수(touched·compute_progress·day_range)는 DB 없이, met 전이·알림 발행·
관측 창 경계는 sqlite 로 검증한다.
"""

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Galae, Note, Notification, Scenario, SeriesSnapshot
from app.series.evaluate import compute_progress, day_range, evaluate_auto, touched

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
TODAY = date.today()


# ── day_range — 그날의 도달 범위 ────────────────────────────────────────────


def test_day_range_uses_low_high_when_present() -> None:
    assert day_range(D("100"), D("105"), D("98")) == (D("98"), D("105"))


def test_day_range_macro_is_close_close() -> None:
    # 거시 계열(has_intraday=false)은 close 하나가 그날의 값 (§5.2)
    assert day_range(D("3.5"), None, None) == (D("3.5"), D("3.5"))


# ── touched — comparator 4종 (§5.2 표 그대로) ───────────────────────────────


def _touch(lo: str, hi: str, comparator: str, **kw: object) -> bool:
    params: dict[str, D | None] = {"target_value": None, "target_low": None, "target_high": None}
    params.update({k: D(str(v)) for k, v in kw.items() if k != "base"})
    base = D(str(kw["base"])) if "base" in kw else None
    return touched(
        D(lo),
        D(hi),
        comparator,
        params["target_value"],
        params["target_low"],
        params["target_high"],
        base,
    )


def test_touched_gte_is_high_reaches_target() -> None:
    assert _touch("100", "110", "gte", target_value=110)
    assert not _touch("100", "109.99", "gte", target_value=110)


def test_touched_lte_is_low_reaches_target() -> None:
    assert _touch("89", "95", "lte", target_value=90)
    assert not _touch("90.01", "95", "lte", target_value=90)


def test_touched_between_is_interval_intersection() -> None:
    # 스쳐 지나가도 닿은 것이다 — [lo,hi] ∩ [low,high] ≠ ∅
    assert _touch("90", "96", "between", target_low=95, target_high=100)
    assert _touch("99", "120", "between", target_low=95, target_high=100)
    assert _touch("90", "120", "between", target_low=95, target_high=100)  # 관통
    assert not _touch("90", "94.9", "between", target_low=95, target_high=100)
    assert _touch("100", "110", "between", target_low=95, target_high=100)  # 경계 포함


def test_touched_change_pct_sign_is_direction() -> None:
    # 양수: (hi−base)/base×100 >= target — 음수: (lo−base)/base×100 <= target
    assert _touch("100", "110", "change_pct", target_value=10, base=100)
    assert not _touch("100", "109.9", "change_pct", target_value=10, base=100)
    assert _touch("90", "100", "change_pct", target_value=-10, base=100)
    assert not _touch("90.1", "100", "change_pct", target_value=-10, base=100)


def test_touched_change_pct_without_base_is_false() -> None:
    # 기준값이 없으면 판정 불능 — 내일 소급된다
    assert not _touch("100", "200", "change_pct", target_value=10)


def test_touched_unknown_comparator_raises() -> None:
    with pytest.raises(ValueError):
        _touch("1", "2", "median")


# ── compute_progress — 목표까지의 거리 0~1 (§5.3) ──────────────────────────


def _progress(
    comparator: str, start: str, max_hi: str, min_lo: str, **kw: object
) -> float | None:
    params: dict[str, D | None] = {"target_value": None, "target_low": None, "target_high": None}
    params.update({k: D(str(v)) for k, v in kw.items() if k != "base"})
    base = D(str(kw["base"])) if "base" in kw else None
    return compute_progress(
        comparator,
        D(start),
        D(max_hi),
        D(min_lo),
        params["target_value"],
        params["target_low"],
        params["target_high"],
        base,
    )


def test_progress_gte_is_distance_ratio() -> None:
    assert _progress("gte", "100", "105", "95", target_value=110) == 0.5


def test_progress_gte_clamps_to_unit_interval() -> None:
    assert _progress("gte", "100", "120", "95", target_value=110) == 1.0  # 초과 달성
    assert _progress("gte", "100", "90", "80", target_value=110) == 0.0  # 반대 방향


def test_progress_zero_denominator_is_one() -> None:
    # 설정 시점에 이미 목표 — 분모 0 이면 1.0
    assert _progress("gte", "110", "111", "100", target_value=110) == 1.0
    assert _progress("lte", "90", "95", "91", target_value=90) == 1.0


def test_progress_lte_is_symmetric() -> None:
    assert _progress("lte", "100", "105", "95", target_value=90) == 0.5


def test_progress_between_uses_nearest_boundary() -> None:
    assert _progress("between", "80", "90", "78", target_low=100, target_high=120) == 0.5
    assert _progress("between", "140", "142", "130", target_low=100, target_high=120) == 0.5
    assert _progress("between", "110", "112", "108", target_low=100, target_high=120) == 1.0


def test_progress_change_pct_is_achieved_over_target() -> None:
    assert _progress("change_pct", "100", "105", "95", target_value=10, base=100) == 0.5
    assert _progress("change_pct", "100", "105", "95", target_value=-10, base=100) == 0.5
    assert _progress("change_pct", "100", "101", "99", target_value=0, base=100) == 1.0


def test_progress_change_pct_without_base_is_none() -> None:
    assert _progress("change_pct", "100", "105", "95", target_value=10) is None


# ── 오케스트레이션 — met 전이·단조성·관측 창 (sqlite) ──────────────────────


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


def _auto_scenario(
    *,
    created: date,
    comparator: str = "gte",
    target_value: str | None = "110",
    provider: str = "kis",
    code: str = "005930",
    baseline_date: date | None = None,
) -> Scenario:
    return Scenario(
        name="목표 도달",
        resolution_type="auto",
        series_provider=provider,
        series_code=code,
        comparator=comparator,
        target_value=D(target_value) if target_value is not None else None,
        baseline_date=baseline_date,
        created_at=_dt(created),
    )


def _snap(
    d: date, close: str, high: str | None = None, low: str | None = None, code: str = "005930"
) -> SeriesSnapshot:
    return SeriesSnapshot(
        provider="kis",
        code=code,
        date=d,
        close=D(close),
        high=D(high) if high is not None else None,
        low=D(low) if low is not None else None,
    )


def test_met_transition_is_monotonic_and_emits_notification(
    series_db: async_sessionmaker[AsyncSession],
) -> None:
    created = TODAY - timedelta(days=10)

    async def _run() -> None:
        scenario = _auto_scenario(created=created)
        async with series_db() as s:
            note = _note()
            note.galae = [Galae(question="넘나?", judge_end=TODAY + timedelta(days=30))]
            note.galae[0].scenarios = [scenario]
            s.add(note)
            s.add_all(
                [
                    _snap(created + timedelta(days=1), "100", "105", "99"),
                    _snap(created + timedelta(days=2), "108", "111", "104"),  # 장중 터치
                    _snap(created + timedelta(days=3), "100", "102", "98"),  # 되돌림
                ]
            )
            await s.commit()
            scenario_id = scenario.id

        async with series_db() as s:
            assert await evaluate_auto(s) == 1

        async with series_db() as s:
            row = await s.get(Scenario, scenario_id)
            assert row is not None
            assert row.auto_status == "met"
            assert row.met_at == created + timedelta(days=2)  # 실제 닿은 날짜
            assert row.progress == 1.0
            notes = (await s.scalars(select(Notification))).all()
            assert len(notes) == 1
            assert notes[0].kind == "auto_condition_met"
            assert notes[0].payload["scenario_id"] == str(scenario_id)
            assert notes[0].user_id == USER_ID

        # met 은 단조다 — 목표 아래 스냅샷이 뒤에 와도 되돌림·중복 알림이 없다
        async with series_db() as s:
            s.add(_snap(created + timedelta(days=4), "50", "51", "49"))
            await s.commit()
        async with series_db() as s:
            assert await evaluate_auto(s) == 0
        async with series_db() as s:
            row = await s.get(Scenario, scenario_id)
            assert row is not None
            assert row.auto_status == "met"
            assert row.met_at == created + timedelta(days=2)
            assert len((await s.scalars(select(Notification))).all()) == 1

    asyncio.run(_run())


def test_window_excludes_before_set_date_and_after_judge_end(
    series_db: async_sessionmaker[AsyncSession],
) -> None:
    created = TODAY - timedelta(days=10)
    judge_end = TODAY - timedelta(days=5)

    async def _run() -> None:
        scenario = _auto_scenario(created=created)
        async with series_db() as s:
            note = _note()
            note.galae = [Galae(question="넘나?", judge_end=judge_end)]
            note.galae[0].scenarios = [scenario]
            s.add(note)
            s.add_all(
                [
                    _snap(created - timedelta(days=2), "120", "125", "118"),  # 설정일 이전 터치
                    _snap(created + timedelta(days=1), "100", "105", "99"),
                    _snap(judge_end + timedelta(days=1), "115", "120", "112"),  # 판단 시점 이후
                ]
            )
            await s.commit()
            scenario_id = scenario.id

        async with series_db() as s:
            assert await evaluate_auto(s) == 0
        async with series_db() as s:
            row = await s.get(Scenario, scenario_id)
            assert row is not None
            assert row.auto_status == "not_met"
            assert row.met_at is None
            # 시작값 = 설정일 이전의 마지막 close(120) — 이미 목표 위 → 분모 0 → 1.0
            assert row.progress == 1.0

    asyncio.run(_run())


def test_progress_start_is_last_close_before_set_date(
    series_db: async_sessionmaker[AsyncSession],
) -> None:
    created = TODAY - timedelta(days=10)

    async def _run() -> None:
        scenario = _auto_scenario(created=created)  # gte 110
        async with series_db() as s:
            note = _note()
            note.galae = [Galae(question="넘나?", judge_end=TODAY + timedelta(days=30))]
            note.galae[0].scenarios = [scenario]
            s.add(note)
            s.add_all(
                [
                    _snap(created - timedelta(days=1), "100", "101", "99"),  # 시작값 100
                    _snap(created + timedelta(days=1), "104", "105", "103"),  # 최고 도달 105
                ]
            )
            await s.commit()
            scenario_id = scenario.id

        async with series_db() as s:
            await evaluate_auto(s)
        async with series_db() as s:
            row = await s.get(Scenario, scenario_id)
            assert row is not None
            assert row.progress == 0.5  # (105−100)/(110−100)

    asyncio.run(_run())


def test_macro_series_evaluates_on_close_only(
    series_db: async_sessionmaker[AsyncSession],
) -> None:
    created = TODAY - timedelta(days=10)

    async def _run() -> None:
        scenario = _auto_scenario(
            created=created, provider="fred", code="DFF", target_value="5"
        )
        async with series_db() as s:
            note = _note()
            note.galae = [Galae(question="금리?", judge_end=TODAY + timedelta(days=30))]
            note.galae[0].scenarios = [scenario]
            s.add(note)
            s.add(
                SeriesSnapshot(
                    provider="fred",
                    code="DFF",
                    date=created + timedelta(days=1),
                    close=D("5.1"),
                    high=None,
                    low=None,
                )
            )
            await s.commit()
            scenario_id = scenario.id

        async with series_db() as s:
            assert await evaluate_auto(s) == 1
        async with series_db() as s:
            row = await s.get(Scenario, scenario_id)
            assert row is not None and row.auto_status == "met"

    asyncio.run(_run())


def test_judged_galae_is_not_evaluated(series_db: async_sessionmaker[AsyncSession]) -> None:
    created = TODAY - timedelta(days=10)

    async def _run() -> None:
        scenario = _auto_scenario(created=created)
        async with series_db() as s:
            note = _note()
            note.galae = [
                Galae(question="넘나?", judge_end=TODAY - timedelta(days=1), status="judged")
            ]
            note.galae[0].scenarios = [scenario]
            s.add(note)
            s.add(_snap(created + timedelta(days=1), "120", "125", "118"))
            await s.commit()
            scenario_id = scenario.id

        async with series_db() as s:
            assert await evaluate_auto(s) == 0
        async with series_db() as s:
            row = await s.get(Scenario, scenario_id)
            assert row is not None and row.auto_status is None

    asyncio.run(_run())
