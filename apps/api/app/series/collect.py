"""일일 수집 배치 — 대상 산출·증분 수집·멱등 upsert (05 §4).

소급 수집이 재시도 전략의 전부다: 어제 배치가 죽었어도 오늘 배치가 `last + 1일`부터
가져오므로 구멍이 저절로 메워진다. provider 한 곳 장애는 계열 단위로 격리한다 —
실패한 계열만 건너뛰고 나머지는 진행한다 (§8).
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Galae, Scenario, SeriesSnapshot, Watch
from app.series.providers import SeriesProvider, build_providers

logger = logging.getLogger(__name__)

BACKFILL_MARGIN_DAYS = 30  # 최초 백필: 참조 최소 기록일 − 30일 (§4.3)


@dataclass(frozen=True)
class CollectTarget:
    provider: str
    code: str
    first_referenced: date  # 그 계열을 참조하는 가장 이른 기록일


@dataclass
class CollectStats:
    targets: int = 0
    succeeded: int = 0
    failed: int = 0
    rows: int = 0


def is_kr_code(code: str) -> bool:
    """kis 계열의 시장 구분 휴리스틱 — 국내 코드는 전부 숫자(주식 6자리·지수 4자리),
    해외는 알파벳 티커다. series_catalog 에 market 칼럼이 없어 코드 형태로 가른다."""
    return code.isdigit()


async def collect_targets(session: AsyncSession) -> list[CollectTarget]:
    """수집 대상: 열린 갈래의 auto 시나리오가 참조하는 계열 ∪ 지켜보는 수치 (§4.2).

    auto_status='not_met' 으로 거르지 않는다 — met 이 된 뒤에도 갈래가 판정되기
    전까지 추이 차트는 계속 자라야 하므로 수집은 갈래 open 기준이다.
    """
    scenario_rows = (
        await session.execute(
            select(
                Scenario.series_provider,
                Scenario.series_code,
                func.min(Scenario.created_at),
            )
            .join(Galae)
            .where(
                Scenario.resolution_type == "auto",
                Scenario.series_provider.is_not(None),
                Scenario.series_code.is_not(None),
                Galae.status == "open",
            )
            .group_by(Scenario.series_provider, Scenario.series_code)
        )
    ).all()
    watch_rows = (
        await session.execute(
            select(Watch.provider, Watch.code, func.min(Watch.created_at)).group_by(
                Watch.provider, Watch.code
            )
        )
    ).all()

    earliest: dict[tuple[str, str], date] = {}

    def _fold(provider: str, code: str, created_at: datetime) -> None:
        key = (provider, code)
        d = created_at.date()
        if key not in earliest or d < earliest[key]:
            earliest[key] = d

    for s_row in scenario_rows:
        _fold(s_row[0], s_row[1], s_row[2])
    for w_row in watch_rows:
        _fold(w_row[0], w_row[1], w_row[2])
    return [
        CollectTarget(provider=p, code=c, first_referenced=d)
        for (p, c), d in sorted(earliest.items())
    ]


async def _collect_one(
    session: AsyncSession, provider: SeriesProvider, target: CollectTarget
) -> int:
    last = await session.scalar(
        select(func.max(SeriesSnapshot.date)).where(
            SeriesSnapshot.provider == target.provider,
            SeriesSnapshot.code == target.code,
        )
    )
    start = (
        last + timedelta(days=1)
        if last is not None
        else target.first_referenced - timedelta(days=BACKFILL_MARGIN_DAYS)
    )
    end = date.today()
    if start > end:
        return 0
    bars = await asyncio.to_thread(provider.fetch_daily, target.code, start, end)
    for bar in bars:
        # 멱등 upsert — 수정치(revision) 발표도 이 경로로 최신 값으로 덮인다 (§4.3)
        await session.merge(
            SeriesSnapshot(
                provider=target.provider,
                code=target.code,
                date=bar["date"],
                close=bar["close"],
                high=bar["high"],
                low=bar["low"],
            )
        )
    await session.commit()
    return len(bars)


async def collect(
    session: AsyncSession, providers_filter: set[str], market: str | None = None
) -> CollectStats:
    """잡별 진입점 — providers_filter 로 provider 를, market('kr'|'us')으로 kis 계열을 가른다."""
    providers = build_providers()
    stats = CollectStats()
    targets = [t for t in await collect_targets(session) if t.provider in providers_filter]
    if market is not None:
        targets = [t for t in targets if is_kr_code(t.code) == (market == "kr")]
    stats.targets = len(targets)
    started = time.monotonic()
    for target in targets:
        provider = providers.get(target.provider)
        if provider is None:
            logger.info("provider 미가용 — 스킵: %s/%s", target.provider, target.code)
            stats.failed += 1
            continue
        try:
            rows = await _collect_one(session, provider, target)
        except NotImplementedError:
            logger.info("provider 미구현 — 스킵: %s/%s", target.provider, target.code)
            await session.rollback()
            stats.failed += 1
        except Exception:
            # 계열 단위 격리 — 실패한 계열만 건너뛰고 나머지는 진행한다 (§8)
            logger.exception("수집 실패: %s/%s", target.provider, target.code)
            await session.rollback()
            stats.failed += 1
        else:
            stats.succeeded += 1
            stats.rows += rows
    logger.info(
        "collect 완료: 대상 %d, 성공 %d, 실패 %d, 적재 %d행, %.1fs",
        stats.targets,
        stats.succeeded,
        stats.failed,
        stats.rows,
        time.monotonic() - started,
    )
    return stats
