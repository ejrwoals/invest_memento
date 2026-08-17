"""홈 API — 피드 우선순위·타임라인·리마인드 상세 3단·opened_at·keep.

DB 는 sqlite(aiosqlite). 노트 저장은 실제 POST /notes 경로를 태워
interval 규칙 자동 생성까지 함께 검증한다.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth import CurrentUser, current_user
from app.db.models import (
    Base,
    Conversation,
    Galae,
    Note,
    Notification,
    ProbabilityEntry,
    ReminderRule,
    Scenario,
    SeriesSnapshot,
    Watch,
)
from app.db.session import get_session
from app.main import app
from app.series.catalog import ensure_equity_series

USER = CurrentUser(id="11111111-1111-1111-1111-111111111111", email="dev@example.com")
USER_ID = UUID(USER.id)
TODAY = date.today()


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/home.db", poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create())

    async def _session() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    app.dependency_overrides[current_user] = lambda: USER
    app.dependency_overrides[get_session] = _session
    yield maker
    app.dependency_overrides.clear()


@pytest.fixture()
def client(db: async_sessionmaker[AsyncSession]) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def run[T](
    maker: async_sessionmaker[AsyncSession], fn: Callable[[AsyncSession], Awaitable[T]]
) -> T:
    async def _inner() -> T:
        async with maker() as session:
            return await fn(session)

    return asyncio.run(_inner())


def _note(user_id: UUID, target: str, judge_end: date | None, color: str = "#2563eb") -> Note:
    note = Note(
        user_id=user_id,
        target_type="ticker",
        target_name=target,
        thesis_summary=f"{target} 가설",
        color=color,
    )
    galae = Galae(
        question=f"{target} 은 어떻게 되는가?",
        judge_kind="date" if judge_end else None,
        judge_end=judge_end,
    )
    galae.scenarios = [
        Scenario(name="그렇게 된다", resolution_type="manual", position=0),
        Scenario(
            name="그 외 예상 못한 전개",
            resolution_type="complement",
            is_residual=True,
            position=1,
        ),
    ]
    note.galae = [galae]
    return note


# ── GET /home ───────────────────────────────────────────────────────────────


def test_home_feed_priority_timeline_and_draft(
    client: TestClient, db: async_sessionmaker[AsyncSession]
) -> None:
    async def _seed(session: AsyncSession) -> str:
        pending = _note(USER_ID, "펜딩", TODAY - timedelta(days=2))
        pending.galae[0].scenarios[0].status = "pending_judgment"
        imminent = _note(USER_ID, "임박", TODAY + timedelta(days=5))
        met = _note(USER_ID, "조건달성", TODAY + timedelta(days=60))
        met.galae[0].scenarios[0].resolution_type = "auto"
        met.galae[0].scenarios[0].series_provider = "kis"
        met.galae[0].scenarios[0].series_code = "005930"
        met.galae[0].scenarios[0].auto_status = "met"
        met.galae[0].scenarios[0].met_at = TODAY - timedelta(days=1)
        interval = _note(USER_ID, "정기", TODAY + timedelta(days=120))
        past_only = _note(USER_ID, "남의노트", TODAY + timedelta(days=9))
        past_only.user_id = UUID("99999999-9999-9999-9999-999999999999")
        session.add_all([pending, imminent, met, interval, past_only])
        await session.flush()
        session.add(
            ReminderRule(
                note_id=interval.id,
                type="interval",
                next_trigger_at=datetime.now(UTC) - timedelta(hours=1),
                current_interval_weeks=2,
            )
        )
        conv = Conversation(user_id=USER_ID, status="draft")
        session.add(conv)
        await session.commit()
        return str(conv.id)

    draft_id = run(db, _seed)

    res = client.get("/home")
    assert res.status_code == 200
    body = res.json()

    # 피드: 결과 확인 필요 > auto 조건 달성 > 시점 임박 > 정기 (ux §3.1)
    assert [c["kind"] for c in body["feed"]] == [
        "pending_judgment",
        "auto_condition_met",
        "deadline",
        "interval",
    ]
    assert [c["title"] for c in body["feed"]] == ["펜딩", "조건달성", "임박", "정기"]
    assert all(c["reason"] for c in body["feed"])  # 이유 없는 알림은 소음이다

    # 타임라인: 판단 시점 오름차순, 지난 갈래(펜딩)와 남의 노트는 제외
    titles = [t["note_title"] for t in body["timeline"]]
    assert titles == ["임박", "조건달성", "정기"]
    assert body["timeline"][0]["judge_end"] == (TODAY + timedelta(days=5)).isoformat()
    assert all(t["color"] and t["question"] for t in body["timeline"])

    assert body["draft_conversation_id"] == draft_id


def test_home_empty_state(client: TestClient) -> None:
    res = client.get("/home")
    assert res.status_code == 200
    assert res.json() == {"feed": [], "timeline": [], "draft_conversation_id": None}


# ── POST /notes → interval 규칙 자동 생성 ──────────────────────────────────


def test_create_note_creates_interval_rule(
    client: TestClient, db: async_sessionmaker[AsyncSession]
) -> None:
    res = client.post(
        "/notes",
        json={
            "target_type": "ticker",
            "target_name": "삼성전자",
            "thesis_summary": "HBM4 진입이 리레이팅을 만든다",
            "galae": [
                {
                    "question": "올해 안에 진입하는가?",
                    "judge_kind": "date",
                    "judge_end": "2026-12-31",
                    "scenarios": [{"name": "진입한다"}, {"name": "밀린다"}],
                }
            ],
            "premises": [{"statement": "공급 부족이 이어져야 한다"}],
        },
    )
    assert res.status_code == 201
    note_id = UUID(res.json()["id"])

    rule = run(
        db, lambda s: s.scalar(select(ReminderRule).where(ReminderRule.note_id == note_id))
    )
    assert rule is not None
    assert rule.type == "interval"
    assert rule.current_interval_weeks == 2
    assert rule.consecutive_unopened == 0
    assert rule.next_trigger_at is not None
    expected = (datetime.now(UTC) + timedelta(weeks=2)).replace(tzinfo=None)
    assert abs((rule.next_trigger_at.replace(tzinfo=None) - expected).total_seconds()) < 60


# ── GET /reminders/{id} — 상세 3단 + opened_at ─────────────────────────────


def _seed_reminder(session: AsyncSession) -> Awaitable[tuple[str, str]]:
    async def _inner() -> tuple[str, str]:
        note = _note(USER_ID, "삼성전자", TODAY + timedelta(days=30))
        auto = note.galae[0].scenarios[0]
        auto.resolution_type = "auto"
        auto.series_provider = "kis"
        auto.series_code = "005930"
        auto.series_label = "삼성전자"
        auto.comparator = "gte"
        auto.target_value = Decimal("95000")
        auto.auto_status = "not_met"
        auto.progress = 0.4
        auto.probability = 60
        note.galae[0].scenarios[1].probability = 40
        note.watches = [Watch(provider="fred", code="DFF", label="미국 기준금리")]
        session.add(note)
        await ensure_equity_series(session, "kis", "005930")
        session.add(
            SeriesSnapshot(
                provider="kis",
                code="005930",
                date=TODAY - timedelta(days=1),
                close=Decimal("83000"),
                high=Decimal("84000"),
                low=Decimal("82000"),
            )
        )
        await session.flush()
        notification = Notification(
            user_id=USER_ID,
            note_id=note.id,
            kind="reminder_digest",
            payload={"items": [{"kind": "interval", "note_id": str(note.id)}]},
            scheduled_for=datetime.now(UTC),
        )
        session.add(notification)
        await session.commit()
        return str(note.id), str(notification.id)

    return _inner()


def test_reminder_detail_three_parts_and_opened_at(
    client: TestClient, db: async_sessionmaker[AsyncSession]
) -> None:
    note_id, notification_id = run(db, lambda s: _seed_reminder(s))

    res = client.get(f"/reminders/{notification_id}")
    assert res.status_code == 200
    body = res.json()

    # ① 당시의 나 — 가설·당시 확률 원본 그대로
    assert body["then"]["thesis_summary"] == "삼성전자 가설"
    probs = {s["name"]: s["probability"] for s in body["then"]["galae"][0]["scenarios"]}
    assert probs == {"그렇게 된다": 60, "그 외 예상 못한 전개": 40}

    # ② 그동안의 일 — 수치만 (현재값·진행도·watch 최신값)
    (auto,) = body["since"]["auto"]
    assert auto["current_value"] == "83000.0000"
    assert auto["target_value"] == "95000.0000"
    assert auto["progress"] == 0.4
    assert auto["met"] is False
    (watch,) = body["since"]["watches"]
    assert watch["label"] == "미국 기준금리"
    assert watch["current_value"] is None  # 스냅샷 없는 계열은 빈 칸 — 지어내지 않는다

    # ③ 액션
    assert body["action"]["note_id"] == note_id
    assert body["action"]["note_url"].endswith(f"/notes/{note_id}")
    assert body["action"]["keep_url"] == f"/reminders/{notification_id}/keep"

    # 첫 조회가 opened_at 을 기록하고, 두 번째 조회는 덮어쓰지 않는다
    first_opened = body["opened_at"]
    assert first_opened is not None
    again = client.get(f"/reminders/{notification_id}").json()
    # sqlite 는 tz 접미사를 떨궈 돌려준다 — 시각 자체가 같은지만 본다
    assert again["opened_at"].rstrip("Z") == first_opened.rstrip("Z")

    opened = run(
        db,
        lambda s: s.scalar(
            select(Notification.opened_at).where(Notification.id == UUID(notification_id))
        ),
    )
    assert opened is not None


def test_reminder_detail_scoped_to_owner(
    client: TestClient, db: async_sessionmaker[AsyncSession]
) -> None:
    _, notification_id = run(db, lambda s: _seed_reminder(s))
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="22222222-2222-2222-2222-222222222222", email="other@example.com"
    )
    assert client.get(f"/reminders/{notification_id}").status_code == 404
    assert client.post(f"/reminders/{notification_id}/keep").status_code == 404


def test_reminder_unknown_id_is_404(client: TestClient) -> None:
    assert client.get(f"/reminders/{uuid4()}").status_code == 404


# ── POST /reminders/{id}/keep ──────────────────────────────────────────────


def test_keep_recalculates_next_trigger_without_probability_entries(
    client: TestClient, db: async_sessionmaker[AsyncSession]
) -> None:
    note_id, notification_id = run(db, lambda s: _seed_reminder(s))

    async def _add_rule(session: AsyncSession) -> None:
        session.add(
            ReminderRule(
                note_id=UUID(note_id),
                type="interval",
                next_trigger_at=datetime.now(UTC) - timedelta(weeks=1),
                current_interval_weeks=4,
            )
        )
        await session.commit()

    run(db, _add_rule)

    res = client.post(f"/reminders/{notification_id}/keep")
    assert res.status_code == 200
    assert res.json()["note_id"] == note_id

    async def _state(session: AsyncSession) -> tuple[ReminderRule | None, int, Any]:
        rule = await session.scalar(
            select(ReminderRule).where(
                ReminderRule.note_id == UUID(note_id), ReminderRule.type == "interval"
            )
        )
        entries = len((await session.scalars(select(ProbabilityEntry))).all())
        opened = await session.scalar(
            select(Notification.opened_at).where(Notification.id == UUID(notification_id))
        )
        return rule, entries, opened

    rule, entries, opened = run(db, _state)
    assert rule is not None and rule.next_trigger_at is not None
    # 검토일 갱신 — 현재 주기(4주) 기준으로 재계산. 감쇠 상태는 건드리지 않는다.
    expected = (datetime.now(UTC) + timedelta(weeks=4)).replace(tzinfo=None)
    assert abs((rule.next_trigger_at.replace(tzinfo=None) - expected).total_seconds()) < 60
    assert rule.current_interval_weeks == 4
    assert entries == 0  # 확률 이력을 만들지 않는다
    assert opened is not None  # `그대로 봅니다`도 본 것이다


def test_keep_creates_rule_when_missing(
    client: TestClient, db: async_sessionmaker[AsyncSession]
) -> None:
    note_id, notification_id = run(db, lambda s: _seed_reminder(s))
    res = client.post(f"/reminders/{notification_id}/keep")
    assert res.status_code == 200
    rule = run(
        db,
        lambda s: s.scalar(select(ReminderRule).where(ReminderRule.note_id == UUID(note_id))),
    )
    assert rule is not None and rule.current_interval_weeks == 2
