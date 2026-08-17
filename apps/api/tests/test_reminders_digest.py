"""일일 다이제스트 잡 — 수집·우선순위·하루 1건 묶음·미열람 감쇠.

인앱 전용 — 이메일 발송 경로는 없다. DB 는 sqlite(aiosqlite).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Galae, Note, Notification, ReminderRule, Scenario
from app.reminders.digest import run_daily_digest

USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")

NOW = datetime(2026, 8, 17, 0, 30, tzinfo=UTC)
TODAY = NOW.date()


def run[T](
    maker: async_sessionmaker[AsyncSession], fn: Callable[[AsyncSession], Awaitable[T]]
) -> T:
    async def _inner() -> T:
        async with maker() as session:
            return await fn(session)

    return asyncio.run(_inner())


def make_note(
    user_id: UUID = USER_A,
    target: str = "삼성전자",
    judge_end: date | None = None,
    scenario_status: str = "active",
    galae_status: str = "open",
) -> Note:
    note = Note(
        user_id=user_id,
        target_type="ticker",
        target_name=target,
        thesis_summary=f"{target} 가설",
        color="#2563eb",
    )
    galae = Galae(
        question=f"{target} 은 어떻게 되는가?",
        judge_kind="date" if judge_end else None,
        judge_end=judge_end,
        status=galae_status,
    )
    galae.scenarios = [
        Scenario(name="그렇게 된다", resolution_type="manual", status=scenario_status, position=0),
        Scenario(
            name="그 외 예상 못한 전개",
            resolution_type="complement",
            is_residual=True,
            status=scenario_status,
            position=1,
        ),
    ]
    note.galae = [galae]
    return note


# ── 수집·우선순위·하루 1통 ──────────────────────────────────────────────────


def test_digest_bundles_by_priority_one_per_day(
    series_db: async_sessionmaker[AsyncSession],
) -> None:
    async def _seed(session: AsyncSession) -> None:
        pending = make_note(target="펜딩노트", judge_end=TODAY - timedelta(days=3))
        pending.galae[0].scenarios[0].status = "pending_judgment"
        imminent = make_note(target="임박노트", judge_end=TODAY + timedelta(days=3))
        session.add_all([pending, imminent])
        await session.flush()
        # 펜딩 노트에는 due 지난 interval 규칙도 있다 — 최고 우선순위 하나로 접혀야 한다
        session.add(
            ReminderRule(
                note_id=pending.id,
                type="interval",
                next_trigger_at=NOW - timedelta(days=1),
                current_interval_weeks=2,
            )
        )
        await session.commit()

    run(series_db, _seed)

    async def _run(session: AsyncSession) -> list[Notification]:
        return await run_daily_digest(session, now=NOW)

    (created,) = run(series_db, _run)
    items = created.payload["items"]
    assert [i["kind"] for i in items] == ["pending_judgment", "deadline"]
    assert created.kind == "reminder_digest"
    assert created.channel == "in_app"  # 이메일 없음 — 인앱 전용 (011)
    assert created.sent_at is not None  # 행 생성 시각 = 인앱 노출 가능 시각
    # note_id 는 최고 우선순위 항목의 노트
    assert str(created.note_id) == items[0]["note_id"]
    # 카드마다 이유 한 줄이 반드시 있다
    assert all(i["reason"] for i in items)

    # 같은 날 다시 돌리면 새 다이제스트가 생기지 않는다 (하루 1건·멱등)
    again = run(series_db, _run)
    assert again == []


def test_digest_deadline_window_and_users_split(
    series_db: async_sessionmaker[AsyncSession],
) -> None:
    async def _seed(session: AsyncSession) -> None:
        session.add_all(
            [
                make_note(user_id=USER_A, target="디데이", judge_end=TODAY),
                make_note(user_id=USER_A, target="팔일뒤", judge_end=TODAY + timedelta(days=8)),
                make_note(user_id=USER_B, target="남의칠일", judge_end=TODAY + timedelta(days=7)),
            ]
        )
        await session.commit()

    run(series_db, _seed)
    created = run(series_db, lambda s: run_daily_digest(s, now=NOW))
    by_user = {n.user_id: n for n in created}
    assert set(by_user) == {USER_A, USER_B}  # 사용자별로 따로 1건
    a_items = by_user[USER_A].payload["items"]
    assert [i["title"] for i in a_items] == ["디데이"]  # D+8 은 창 밖
    assert "오늘이 판단 시점" in a_items[0]["reason"]
    b_items = by_user[USER_B].payload["items"]
    assert "7일 남았습니다" in b_items[0]["reason"]


def test_digest_interval_needs_judge_end(series_db: async_sessionmaker[AsyncSession]) -> None:
    async def _seed(session: AsyncSession) -> None:
        dated = make_note(target="시점있음", judge_end=TODAY + timedelta(days=90))
        undated = make_note(target="시점없음", judge_end=None)
        session.add_all([dated, undated])
        await session.flush()
        for n in (dated, undated):
            session.add(
                ReminderRule(
                    note_id=n.id,
                    type="interval",
                    next_trigger_at=NOW - timedelta(hours=1),
                    current_interval_weeks=2,
                )
            )
        await session.commit()

    run(series_db, _seed)
    (created,) = run(series_db, lambda s: run_daily_digest(s, now=NOW))
    items = created.payload["items"]
    # 판단 시점이 없는 노트는 리마인드 대상이 아니다 (dev-plan §2.4)
    assert [i["title"] for i in items] == ["시점있음"]
    assert items[0]["kind"] == "interval"


def test_digest_consumes_unsent_auto_met(series_db: async_sessionmaker[AsyncSession]) -> None:
    async def _seed(session: AsyncSession) -> UUID:
        note = make_note(target="조건달성", judge_end=TODAY + timedelta(days=90))
        session.add(note)
        await session.flush()
        scenario = note.galae[0].scenarios[0]
        session.add(
            Notification(
                user_id=USER_A,
                note_id=note.id,
                kind="auto_condition_met",
                payload={
                    "scenario_id": str(scenario.id),
                    "galae_id": str(note.galae[0].id),
                    "met_at": (TODAY - timedelta(days=1)).isoformat(),
                },
                scheduled_for=NOW - timedelta(hours=5),
            )
        )
        await session.commit()
        return note.id

    run(series_db, _seed)
    (created,) = run(series_db, lambda s: run_daily_digest(s, now=NOW))
    (item,) = created.payload["items"]
    assert item["kind"] == "auto_condition_met"
    assert "'그렇게 된다'" in item["reason"]

    async def _source(session: AsyncSession) -> Notification | None:
        return await session.scalar(
            select(Notification).where(Notification.kind == "auto_condition_met")
        )

    source = run(series_db, _source)
    assert source is not None and source.sent_at is not None  # 소비됨 — 내일 또 담기지 않는다

    # 다음날 돌려도 새 항목이 없다
    assert run(series_db, lambda s: run_daily_digest(s, now=NOW + timedelta(days=1))) == []


# ── 미열람 감쇠 ─────────────────────────────────────────────────────────────


def _seed_interval_note(target: str = "감쇠노트") -> Callable[[AsyncSession], Awaitable[UUID]]:
    async def _seed(session: AsyncSession) -> UUID:
        note = make_note(target=target, judge_end=TODAY + timedelta(days=180))
        session.add(note)
        await session.flush()
        session.add(
            ReminderRule(
                note_id=note.id,
                type="interval",
                next_trigger_at=NOW - timedelta(hours=1),
                current_interval_weeks=2,
            )
        )
        await session.commit()
        return note.id

    return _seed


def test_decay_doubles_when_previous_digest_unopened(
    series_db: async_sessionmaker[AsyncSession],
) -> None:
    note_id = run(series_db, _seed_interval_note())

    async def _previous(session: AsyncSession) -> None:
        session.add(
            Notification(
                user_id=USER_A,
                note_id=note_id,
                kind="reminder_digest",
                payload={"items": [{"kind": "interval", "note_id": str(note_id)}]},
                scheduled_for=NOW - timedelta(days=14),
                sent_at=NOW - timedelta(days=14),
                opened_at=None,  # 안 열었다
            )
        )
        await session.commit()

    run(series_db, _previous)
    run(series_db, lambda s: run_daily_digest(s, now=NOW))

    async def _rule(session: AsyncSession) -> ReminderRule:
        rule = await session.scalar(select(ReminderRule).where(ReminderRule.note_id == note_id))
        assert rule is not None
        return rule

    rule = run(series_db, _rule)
    assert rule.consecutive_unopened == 1
    assert rule.current_interval_weeks == 4  # 2 → 4
    assert rule.next_trigger_at is not None
    expected = (NOW + timedelta(weeks=4)).replace(tzinfo=None)
    assert abs((rule.next_trigger_at.replace(tzinfo=None) - expected).total_seconds()) < 5


def test_decay_caps_at_12_weeks(series_db: async_sessionmaker[AsyncSession]) -> None:
    note_id = run(series_db, _seed_interval_note())

    async def _prepare(session: AsyncSession) -> None:
        rule = await session.scalar(select(ReminderRule).where(ReminderRule.note_id == note_id))
        assert rule is not None
        rule.current_interval_weeks = 8
        rule.consecutive_unopened = 2
        session.add(
            Notification(
                user_id=USER_A,
                note_id=note_id,
                kind="reminder_digest",
                payload={"items": [{"kind": "interval", "note_id": str(note_id)}]},
                scheduled_for=NOW - timedelta(weeks=8),
                opened_at=None,
            )
        )
        await session.commit()

    run(series_db, _prepare)
    run(series_db, lambda s: run_daily_digest(s, now=NOW))
    rule = run(
        series_db,
        lambda s: s.scalar(select(ReminderRule).where(ReminderRule.note_id == note_id)),
    )
    assert rule is not None
    assert rule.current_interval_weeks == 12  # min(8×2, 12)
    assert rule.consecutive_unopened == 3


def test_decay_resets_when_previous_digest_opened(
    series_db: async_sessionmaker[AsyncSession],
) -> None:
    note_id = run(series_db, _seed_interval_note())

    async def _prepare(session: AsyncSession) -> None:
        rule = await session.scalar(select(ReminderRule).where(ReminderRule.note_id == note_id))
        assert rule is not None
        rule.current_interval_weeks = 8
        rule.consecutive_unopened = 2
        session.add(
            Notification(
                user_id=USER_A,
                note_id=note_id,
                kind="reminder_digest",
                payload={"items": [{"kind": "interval", "note_id": str(note_id)}]},
                scheduled_for=NOW - timedelta(weeks=8),
                opened_at=NOW - timedelta(weeks=7),  # 열어 봤다
            )
        )
        await session.commit()

    run(series_db, _prepare)
    run(series_db, lambda s: run_daily_digest(s, now=NOW))
    rule = run(
        series_db,
        lambda s: s.scalar(select(ReminderRule).where(ReminderRule.note_id == note_id)),
    )
    assert rule is not None
    assert rule.consecutive_unopened == 0
    assert rule.current_interval_weeks == 2  # 한 번 열면 원래 주기로 즉시 복귀
    assert rule.next_trigger_at is not None
    expected = (NOW + timedelta(weeks=2)).replace(tzinfo=None)
    assert abs((rule.next_trigger_at.replace(tzinfo=None) - expected).total_seconds()) < 5


# ── 인앱 전용 — 문구·행 상태 ────────────────────────────────────────────────


def test_digest_reasons_have_no_pressure_wording(
    series_db: async_sessionmaker[AsyncSession],
) -> None:
    run(series_db, _seed_interval_note())
    (created,) = run(series_db, lambda s: run_daily_digest(s, now=NOW))
    (item,) = created.payload["items"]
    for banned in ("연속", "스트릭", "달성률", "방치"):  # P5 — 압박 문구 금지
        assert banned not in item["reason"]
