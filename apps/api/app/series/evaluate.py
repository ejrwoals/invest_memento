"""auto 조건 평가 — 관측 규칙·comparator 4종·progress·met 전이 (05 §5).

시스템 고정 관측 규칙(§2.3): 판단 시점까지 기간 중 한 번이라도 목표에 닿으면 달성.
장중 포함 — 그날의 도달 범위 [lo, hi]는 주식·지수 [low, high], 거시 [close, close].
met 은 단조다 — 한 번 닿으면 되돌림이 없다. met 은 판정이 아니라 제안의 트리거다.
"""

import logging
import time
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Galae, Note, Notification, Scenario, SeriesSnapshot

logger = logging.getLogger(__name__)


def day_range(close: Decimal, high: Decimal | None, low: Decimal | None) -> tuple[Decimal, Decimal]:
    """그날의 도달 범위 [lo, hi] — 거시 계열(high·low 없음)은 [close, close]."""
    return (low if low is not None else close, high if high is not None else close)


def touched(
    lo: Decimal,
    hi: Decimal,
    comparator: str,
    target_value: Decimal | None,
    target_low: Decimal | None,
    target_high: Decimal | None,
    base: Decimal | None,
) -> bool:
    """comparator 4종의 '그날 닿음' (§5.2 표 그대로)."""
    if comparator == "gte":
        assert target_value is not None
        return hi >= target_value
    if comparator == "lte":
        assert target_value is not None
        return lo <= target_value
    if comparator == "between":
        assert target_low is not None and target_high is not None
        return lo <= target_high and hi >= target_low  # 구간 교집합 — 스쳐 지나가도 닿은 것
    if comparator == "change_pct":
        assert target_value is not None
        if base is None or base == 0:
            return False  # 기준값이 없으면 판정 불능 — 내일 소급된다
        if target_value > 0:
            return (hi - base) / base * 100 >= target_value
        return (lo - base) / base * 100 <= target_value  # 부호가 방향이다
    raise ValueError(f"unknown comparator: {comparator}")


def _clamp(value: Decimal) -> float:
    return float(max(Decimal(0), min(Decimal(1), value)))


def compute_progress(
    comparator: str,
    start: Decimal,
    max_hi: Decimal,
    min_lo: Decimal,
    target_value: Decimal | None,
    target_low: Decimal | None,
    target_high: Decimal | None,
    base: Decimal | None,
) -> float | None:
    """목표까지의 거리 0~1 (§5.3). 분모가 0(설정 시점에 이미 목표)이면 1.0."""
    if comparator == "gte":
        assert target_value is not None
        denom = target_value - start
        if denom <= 0:
            return 1.0
        return _clamp((max_hi - start) / denom)
    if comparator == "lte":
        assert target_value is not None
        denom = start - target_value
        if denom <= 0:
            return 1.0
        return _clamp((start - min_lo) / denom)
    if comparator == "between":
        assert target_low is not None and target_high is not None
        if target_low <= start <= target_high:
            return 1.0
        if start < target_low:  # 가까운 경계 = target_low
            return _clamp((max_hi - start) / (target_low - start))
        return _clamp((start - min_lo) / (start - target_high))
    if comparator == "change_pct":
        assert target_value is not None
        if base is None or base == 0:
            return None
        if target_value == 0:
            return 1.0
        if target_value > 0:
            achieved = (max_hi - base) / base * 100
        else:
            achieved = (min_lo - base) / base * 100
        return _clamp(achieved / target_value)  # 달성 변화율 ÷ 목표 변화율
    raise ValueError(f"unknown comparator: {comparator}")


async def _base_close(
    session: AsyncSession, provider: str, code: str, on_or_before: date
) -> Decimal | None:
    """해당 날짜(포함) 이전의 마지막 close."""
    value: Decimal | None = await session.scalar(
        select(SeriesSnapshot.close)
        .where(
            SeriesSnapshot.provider == provider,
            SeriesSnapshot.code == code,
            SeriesSnapshot.date <= on_or_before,
        )
        .order_by(SeriesSnapshot.date.desc())
        .limit(1)
    )
    return value


async def evaluate_auto(session: AsyncSession) -> int:
    """평가 대상: auto ∧ 미달성 ∧ 갈래 open (§5). met 전이 수를 반환한다.

    조건을 나중에 채운 시나리오도 창 전체를 훑으므로 소급 판정이 저절로 된다 (§5.1).
    데이터가 어제까지뿐이면 어제까지로 평가한다 — met 은 단조라 판정이 틀어지지 않는다.
    """
    started = time.monotonic()
    rows = (
        await session.execute(
            select(Scenario, Galae, Note)
            .join(Galae, Scenario.galae_id == Galae.id)
            .join(Note, Galae.note_id == Note.id)
            .where(
                Scenario.resolution_type == "auto",
                Scenario.series_provider.is_not(None),
                Scenario.series_code.is_not(None),
                Scenario.comparator.is_not(None),
                Scenario.auto_status.is_distinct_from("met"),
                Galae.status == "open",
            )
        )
    ).all()

    transitions = 0
    today = date.today()
    for scenario, galae, note in rows:
        provider, code = scenario.series_provider, scenario.series_code
        assert provider is not None and code is not None and scenario.comparator is not None
        # 관측 창: [조건 설정일, galae.judge_end] — judge_end 미정이면 오늘까지 (§5.1)
        set_date = scenario.created_at.date()
        window_end = min(today, galae.judge_end) if galae.judge_end is not None else today
        snapshots = (
            await session.scalars(
                select(SeriesSnapshot)
                .where(
                    SeriesSnapshot.provider == provider,
                    SeriesSnapshot.code == code,
                    SeriesSnapshot.date >= set_date,
                    SeriesSnapshot.date <= window_end,
                )
                .order_by(SeriesSnapshot.date)
            )
        ).all()
        if not snapshots:
            continue

        base: Decimal | None = None
        if scenario.comparator == "change_pct":
            assert scenario.baseline_date is not None
            base = await _base_close(session, provider, code, scenario.baseline_date)

        met_on: date | None = None
        for snap in snapshots:
            lo, hi = day_range(snap.close, snap.high, snap.low)
            if touched(
                lo,
                hi,
                scenario.comparator,
                scenario.target_value,
                scenario.target_low,
                scenario.target_high,
                base,
            ):
                met_on = snap.date
                break

        if met_on is not None:
            scenario.auto_status = "met"
            scenario.met_at = met_on  # 늦게 잡혀도 실제 닿은 날짜로 기록된다 (§8)
            scenario.progress = 1.0
            # met 전이 → 리마인드 트리거 발행만 한다 (§5.4). 문구·발송·묶음은 리마인드의 일.
            session.add(
                Notification(
                    user_id=note.user_id,
                    note_id=note.id,
                    kind="auto_condition_met",
                    payload={
                        "scenario_id": str(scenario.id),
                        "galae_id": str(galae.id),
                        "met_at": met_on.isoformat(),
                    },
                    scheduled_for=datetime.now(UTC),
                )
            )
            transitions += 1
            continue

        # 미달성 — progress 캐시 갱신. 시작값 = 설정일 이전의 마지막 close (§5.3)
        scenario.auto_status = "not_met"
        start = await session.scalar(
            select(SeriesSnapshot.close)
            .where(
                SeriesSnapshot.provider == provider,
                SeriesSnapshot.code == code,
                SeriesSnapshot.date < set_date,
            )
            .order_by(SeriesSnapshot.date.desc())
            .limit(1)
        )
        if start is None:
            start = snapshots[0].close  # 이전 데이터가 없으면 창의 첫 값으로 근사
        ranges = [day_range(s.close, s.high, s.low) for s in snapshots]
        progress = compute_progress(
            scenario.comparator,
            start,
            max(hi for _, hi in ranges),
            min(lo for lo, _ in ranges),
            scenario.target_value,
            scenario.target_low,
            scenario.target_high,
            base,
        )
        if progress is not None:
            scenario.progress = progress

    await session.commit()
    logger.info(
        "evaluate 완료: 대상 %d, met 전이 %d, %.1fs",
        len(rows),
        transitions,
        time.monotonic() - started,
    )
    return transitions
