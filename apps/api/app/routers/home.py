"""홈(피드+세로 타임라인)과 리마인드 상세 3단 (ux §3.1·§3.5, 02-backend §4).

피드 카드의 우선순위는 ux §3.1 을 따른다:
결과 확인 필요 > auto 조건 달성 > 시점 임박 > 정기 리마인드.
(새 정보 카드는 M8 리서치에서 붙는다.)
타임라인은 열린 갈래의 판단 시점 오름차순 — 지나간 항목은 싣지 않는다 (P5).
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import RequireUser
from app.db import SessionDep
from app.db.models import (
    ContentBlock,
    Conversation,
    Galae,
    Note,
    Notification,
    ReminderRule,
    Scenario,
    SeriesSnapshot,
)
from app.reminders.digest import (
    DEFAULT_INTERVAL_WEEKS,
    DigestItem,
    deadline_items,
    interval_due_items,
    pending_judgment_items,
)

router = APIRouter()

# 홈 피드 우선순위 (ux §3.1) — 다이제스트(②③④ 순서)와 달리 auto 달성이 임박보다 앞이다
_FEED_PRIORITY = {"pending_judgment": 0, "auto_condition_met": 1, "deadline": 2, "interval": 3}


# ── GET /home ───────────────────────────────────────────────────────────────


class FeedCard(BaseModel):
    kind: str
    note_id: UUID
    galae_id: UUID | None
    title: str
    reason: str
    date: date | None


class TimelineEntry(BaseModel):
    judge_end: date
    note_id: UUID
    galae_id: UUID
    note_title: str
    color: str
    question: str


class HomeOut(BaseModel):
    feed: list[FeedCard]
    timeline: list[TimelineEntry]
    draft_conversation_id: UUID | None


async def _met_cards(session: AsyncSession, user_id: UUID) -> list[DigestItem]:
    """auto 조건 달성 카드 — 발송 여부와 무관하게, 아직 판정 전이면 홈에 살아 있다."""
    rows = (
        await session.execute(
            select(Scenario, Galae, Note)
            .join(Galae, Scenario.galae_id == Galae.id)
            .join(Note, Galae.note_id == Note.id)
            .where(
                Scenario.auto_status == "met",
                Scenario.status == "active",
                Galae.status == "open",
                Note.user_id == user_id,
                Note.archived_at.is_(None),
            )
            .order_by(Scenario.met_at)
        )
    ).all()
    return [
        DigestItem(
            kind="auto_condition_met",
            note_id=note.id,
            galae_id=galae.id,
            title=note.target_name,
            reason=f"'{scenario.name}' — 설정한 확인 조건에 닿았습니다.",
            date=scenario.met_at,
        )
        for scenario, galae, note in rows
    ]


@router.get("/home")
async def get_home(user: RequireUser, session: SessionDep) -> HomeOut:
    uid = UUID(user.id)
    today = date.today()
    now = datetime.now(UTC)

    candidates: list[DigestItem] = []
    candidates += [item for _, item in await pending_judgment_items(session, user_id=uid)]
    candidates += await _met_cards(session, uid)
    candidates += [item for _, item in await deadline_items(session, today, user_id=uid)]
    candidates += [item for _, item, _rule in await interval_due_items(session, now, user_id=uid)]

    best: dict[UUID, DigestItem] = {}
    for item in candidates:
        current = best.get(item.note_id)
        if current is None or _FEED_PRIORITY[item.kind] < _FEED_PRIORITY[current.kind]:
            best[item.note_id] = item
    feed = sorted(
        best.values(), key=lambda i: (_FEED_PRIORITY[i.kind], i.date or date.max, str(i.note_id))
    )

    timeline_rows = (
        await session.execute(
            select(Galae, Note)
            .join(Note, Galae.note_id == Note.id)
            .where(
                Galae.status == "open",
                Galae.judge_end.is_not(None),
                Galae.judge_end >= today,  # 지나간 것은 피드(결과 확인 필요)가 다룬다
                Note.user_id == uid,
                Note.archived_at.is_(None),
            )
            .order_by(Galae.judge_end)
        )
    ).all()

    draft_conversation_id = await session.scalar(
        select(Conversation.id)
        .where(Conversation.user_id == uid, Conversation.status == "draft")
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    )

    def _timeline(galae: Galae, note: Note) -> TimelineEntry:
        assert galae.judge_end is not None
        return TimelineEntry(
            judge_end=galae.judge_end,
            note_id=note.id,
            galae_id=galae.id,
            note_title=note.target_name,
            color=note.color,
            question=galae.question,
        )

    return HomeOut(
        feed=[
            FeedCard(
                kind=i.kind,
                note_id=i.note_id,
                galae_id=i.galae_id,
                title=i.title,
                reason=i.reason,
                date=i.date,
            )
            for i in feed
        ],
        timeline=[_timeline(g, n) for g, n in timeline_rows],
        draft_conversation_id=draft_conversation_id,
    )


# ── GET /reminders/{id} — 상세 3단 ─────────────────────────────────────────


class ThenScenarioOut(BaseModel):
    id: UUID
    name: str
    probability: int | None
    is_residual: bool


class ThenGalaeOut(BaseModel):
    id: UUID
    question: str
    judge_end: date | None
    scenarios: list[ThenScenarioOut]


class ThenOut(BaseModel):
    """① 당시의 나 — 원본 그대로, 재요약하지 않는다 (P2)."""

    thesis_summary: str
    quote: str | None
    quote_authorship: str | None
    recorded_at: datetime
    galae: list[ThenGalaeOut]


class AutoNowOut(BaseModel):
    scenario_id: UUID
    scenario_name: str
    series_label: str | None
    comparator: str | None
    target_value: Decimal | None
    target_low: Decimal | None
    target_high: Decimal | None
    current_value: Decimal | None
    current_date: date | None
    progress: float | None
    met: bool
    met_at: date | None


class WatchNowOut(BaseModel):
    watch_id: UUID
    label: str
    current_value: Decimal | None
    current_date: date | None


class SinceOut(BaseModel):
    """② 그동안의 일 — 수치만. 뉴스 리서치는 M8 에서 붙는다."""

    auto: list[AutoNowOut]
    watches: list[WatchNowOut]


class ActionOut(BaseModel):
    """③ 액션 — 다시 판단하기 / 그대로 봅니다 / 나중에 (ux §3.5)."""

    note_id: UUID
    note_url: str
    keep_url: str


class ReminderDetailOut(BaseModel):
    id: UUID
    kind: str
    note_id: UUID
    opened_at: datetime
    then: ThenOut
    since: SinceOut
    action: ActionOut


async def _latest_snapshot(
    session: AsyncSession, provider: str, code: str
) -> SeriesSnapshot | None:
    snap: SeriesSnapshot | None = await session.scalar(
        select(SeriesSnapshot)
        .where(SeriesSnapshot.provider == provider, SeriesSnapshot.code == code)
        .order_by(SeriesSnapshot.date.desc())
        .limit(1)
    )
    return snap


@router.get("/reminders/{notification_id}")
async def get_reminder(
    notification_id: UUID, user: RequireUser, session: SessionDep
) -> ReminderDetailOut:
    notification = await session.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == UUID(user.id)
        )
    )
    if notification is None or notification.note_id is None:
        raise HTTPException(status_code=404, detail="reminder not found")
    note = await session.scalar(
        select(Note)
        .where(Note.id == notification.note_id)
        .options(
            selectinload(Note.galae).selectinload(Galae.scenarios),
            selectinload(Note.watches),
        )
    )
    if note is None:
        raise HTTPException(status_code=404, detail="reminder not found")

    # 첫 조회 시각을 기록한다 — 미열람 감쇠의 근거. 두 번째 조회부터는 건드리지 않는다.
    if notification.opened_at is None:
        notification.opened_at = datetime.now(UTC)

    quote_block = await session.scalar(
        select(ContentBlock)
        .where(ContentBlock.note_id == note.id, ContentBlock.section == "thesis_quote")
        .order_by(ContentBlock.position)
        .limit(1)
    )

    auto_now: list[AutoNowOut] = []
    for galae in note.galae:
        for s in galae.scenarios:
            if s.resolution_type != "auto" or not (s.series_provider and s.series_code):
                continue
            snap = await _latest_snapshot(session, s.series_provider, s.series_code)
            auto_now.append(
                AutoNowOut(
                    scenario_id=s.id,
                    scenario_name=s.name,
                    series_label=s.series_label,
                    comparator=s.comparator,
                    target_value=s.target_value,
                    target_low=s.target_low,
                    target_high=s.target_high,
                    current_value=snap.close if snap else None,
                    current_date=snap.date if snap else None,
                    progress=s.progress,
                    met=s.auto_status == "met",
                    met_at=s.met_at,
                )
            )

    watches_now: list[WatchNowOut] = []
    for w in note.watches:
        snap = await _latest_snapshot(session, w.provider, w.code)
        watches_now.append(
            WatchNowOut(
                watch_id=w.id,
                label=w.label,
                current_value=snap.close if snap else None,
                current_date=snap.date if snap else None,
            )
        )

    await session.commit()  # opened_at 기록 확정

    return ReminderDetailOut(
        id=notification.id,
        kind=notification.kind,
        note_id=note.id,
        opened_at=notification.opened_at,
        then=ThenOut(
            thesis_summary=note.thesis_summary,
            quote=quote_block.content if quote_block else None,
            quote_authorship=quote_block.authorship if quote_block else None,
            recorded_at=note.created_at,
            galae=[
                ThenGalaeOut(
                    id=g.id,
                    question=g.question,
                    judge_end=g.judge_end,
                    scenarios=[
                        ThenScenarioOut(
                            id=s.id,
                            name=s.name,
                            probability=s.probability,
                            is_residual=s.is_residual,
                        )
                        for s in g.scenarios
                    ],
                )
                for g in note.galae
            ],
        ),
        since=SinceOut(auto=auto_now, watches=watches_now),
        action=ActionOut(
            note_id=note.id,
            note_url=f"/notes/{note.id}",  # 프론트 라우트 — 절대 URL 은 클라이언트가 만든다
            keep_url=f"/reminders/{notification.id}/keep",
        ),
    )


# ── POST /reminders/{id}/keep — `그대로 봅니다` ────────────────────────────


class KeepOut(BaseModel):
    note_id: UUID
    next_trigger_at: datetime


@router.post("/reminders/{notification_id}/keep")
async def keep_reminder(notification_id: UUID, user: RequireUser, session: SessionDep) -> KeepOut:
    """읽었고, 안 바꿨다 — 확률 이력을 만들지 않고 검토일만 갱신한다 (ux §3.5)."""
    notification = await session.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == UUID(user.id)
        )
    )
    if notification is None or notification.note_id is None:
        raise HTTPException(status_code=404, detail="reminder not found")

    now = datetime.now(UTC)
    if notification.opened_at is None:
        notification.opened_at = now  # `그대로 봅니다`는 본 것이다

    rule = await session.scalar(
        select(ReminderRule)
        .where(ReminderRule.note_id == notification.note_id, ReminderRule.type == "interval")
        .limit(1)
    )
    if rule is None:
        # 규칙이 없는 옛 노트 — 여기서 만들어 준다 (자기 치유)
        rule = ReminderRule(
            note_id=notification.note_id,
            type="interval",
            consecutive_unopened=0,
            current_interval_weeks=DEFAULT_INTERVAL_WEEKS,
        )
        session.add(rule)
    rule.next_trigger_at = now + timedelta(weeks=rule.current_interval_weeks)
    await session.commit()
    assert rule.next_trigger_at is not None
    return KeepOut(note_id=notification.note_id, next_trigger_at=rule.next_trigger_at)
