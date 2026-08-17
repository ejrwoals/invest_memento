"""워커 프로세스 — APScheduler 크론 (05 §1, §4.1). 워커는 1개를 전제한다.

기동: `uv run python -m app.worker`
수동 1회 실행: `uv run python -m app.worker --once collect_macro`

Redis 없음 — 큐·락·캐시는 전부 Postgres 가 맡는다. 모든 잡은 멱등이고
max_instances=1 로 같은 잡의 중복 실행을 막는다. evaluate 는 각 collect 직후
체이닝하되 수집 실패와 무관하게 항상 실행한다 — 데이터가 어제까지뿐이면
어제까지로 평가한다 (§8).
"""

import argparse
import asyncio
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.session import get_sessionmaker
from app.reminders.digest import run_daily_digest
from app.series.collect import collect
from app.series.evaluate import evaluate_auto
from app.series.transition import transition_judgment

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")


async def collect_kr() -> None:
    """국내 주식·지수 — 평일 17:30 KST (장 마감 후)."""
    maker = get_sessionmaker()
    async with maker() as session:
        await collect(session, providers_filter={"kis"}, market="kr")
    async with maker() as session:
        await evaluate_auto(session)


async def collect_us() -> None:
    """해외 주식·지수 — 평일 07:30 KST (미국장 마감 후)."""
    maker = get_sessionmaker()
    async with maker() as session:
        await collect(session, providers_filter={"kis"}, market="us")
    async with maker() as session:
        await evaluate_auto(session)


async def collect_macro() -> None:
    """fred·ecos 전 계열 — 매일 08:00 KST. 거시는 매일 훑어도 수십 건이다 (§4.1)."""
    maker = get_sessionmaker()
    async with maker() as session:
        await collect(session, providers_filter={"fred", "ecos"})
    async with maker() as session:
        await evaluate_auto(session)


async def evaluate() -> None:
    maker = get_sessionmaker()
    async with maker() as session:
        await evaluate_auto(session)


async def transition() -> None:
    """judge_end 도래 → pending_judgment — 매일 09:00 KST (§5.5)."""
    maker = get_sessionmaker()
    async with maker() as session:
        await transition_judgment(session)


async def digest() -> None:
    """하루 1건 인앱 리마인드 다이제스트 — 매일 09:30 KST (transition 09:00 뒤에 돈다)."""
    maker = get_sessionmaker()
    async with maker() as session:
        await run_daily_digest(session)


JOBS = {
    "collect_kr": collect_kr,
    "collect_us": collect_us,
    "collect_macro": collect_macro,
    "evaluate": evaluate,
    "transition": transition,
    "digest": digest,
}


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=KST)
    scheduler.add_job(
        collect_kr,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=30, timezone=KST),
        id="collect_kr",
        max_instances=1,
    )
    scheduler.add_job(
        collect_us,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=30, timezone=KST),
        id="collect_us",
        max_instances=1,
    )
    scheduler.add_job(
        collect_macro,
        CronTrigger(hour=8, minute=0, timezone=KST),
        id="collect_macro",
        max_instances=1,
    )
    scheduler.add_job(
        transition,
        CronTrigger(hour=9, minute=0, timezone=KST),
        id="transition",
        max_instances=1,
    )
    scheduler.add_job(
        digest,
        CronTrigger(hour=9, minute=30, timezone=KST),
        id="digest",
        max_instances=1,
    )
    return scheduler


async def _run_forever() -> None:
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("워커 기동 — 잡: %s", ", ".join(sorted(j.id for j in scheduler.get_jobs())))
    try:
        await asyncio.Event().wait()  # 크론이 깨울 때까지 대기
    finally:
        scheduler.shutdown(wait=False)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Investment Memento series worker")
    parser.add_argument(
        "--once",
        choices=sorted(JOBS),
        help="잡 하나를 즉시 1회 실행하고 종료한다 (예: --once collect_macro)",
    )
    args = parser.parse_args()
    if args.once:
        asyncio.run(JOBS[args.once]())
    else:
        asyncio.run(_run_forever())


if __name__ == "__main__":
    main()
