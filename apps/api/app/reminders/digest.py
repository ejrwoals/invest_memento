"""일일 리마인드 다이제스트 — 사용자별 하루 1건, 인앱 전용 (ux-design §8, 02-backend §4).

이메일 발송은 없다 — notifications(kind='reminder_digest') 행 생성까지가 잡의 일이고,
이 행이 홈 피드와 /reminders/{id} 상세의 원천이다. sent_at 은 행 생성 시각으로
채운다(인앱에 노출 가능해진 시각) — sent_at null 은 '아직 다이제스트에 담기지 않음'이다.

MVP 는 LLM 0회 — 문구는 전부 템플릿이다 (development-plan §7.2, M5).
수집 → 우선순위 순 dedup(같은 노트는 최고 우선순위 하나만) → 행 insert.

우선순위: ① 결과 확인 필요(pending_judgment) ② 판단 시점 임박·도래(D-7 이내)
③ 정기(interval) ④ auto 조건 달성 미소비분.

미열람 감쇠(P5 — 화면에 드러내지 않는다): interval 항목을 담을 때 그 노트가
담겼던 직전 다이제스트의 opened_at 이 null 이면 주기를 2배로(최대 12주),
열람됐으면 2주로 리셋한다. 스트릭·개수 압박 문구는 쓰지 않는다.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Galae, Note, Notification, ReminderRule, Scenario

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_WEEKS = 2
MAX_INTERVAL_WEEKS = 12
IMMINENT_DAYS = 7  # 판단 시점 임박 = D-7 이내 (ux §8)

# 'date' 필드가 기본값 대입 후 어노테이션 평가에서 date 타입을 가리므로 별칭으로 참조한다
# (db/models.py 의 SeriesSnapshot 과 같은 문제)
DateOnly = date


@dataclass
class DigestItem:
    """다이제스트·홈 피드가 공유하는 카드 한 장 — 왜 지금 떴는지(reason)를 반드시 담는다."""

    kind: str  # 'pending_judgment' | 'deadline' | 'interval' | 'auto_condition_met'
    note_id: UUID
    title: str
    reason: str
    date: DateOnly | None = None
    galae_id: UUID | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "note_id": str(self.note_id),
            "galae_id": str(self.galae_id) if self.galae_id else None,
            "title": self.title,
            "reason": self.reason,
            "date": self.date.isoformat() if self.date else None,
        }


# ── 수집 — 홈 피드(routers/home.py)도 같은 쿼리를 쓴다 ─────────────────────


async def pending_judgment_items(
    session: AsyncSession, user_id: UUID | None = None
) -> list[tuple[UUID, DigestItem]]:
    """① 결과 확인 필요 — pending_judgment 시나리오가 있는 열린 갈래."""
    stmt = (
        select(Galae, Note)
        .join(Note, Galae.note_id == Note.id)
        .where(
            Galae.status == "open",
            Note.archived_at.is_(None),
            Galae.id.in_(select(Scenario.galae_id).where(Scenario.status == "pending_judgment")),
        )
        .order_by(Galae.judge_end)
    )
    if user_id is not None:
        stmt = stmt.where(Note.user_id == user_id)
    rows = (await session.execute(stmt)).all()
    return [
        (
            note.user_id,
            DigestItem(
                kind="pending_judgment",
                note_id=note.id,
                galae_id=galae.id,
                title=note.target_name,
                reason=f"'{galae.question}' — 판단 시점이 지났습니다. 결과를 확인해 주세요.",
                date=galae.judge_end,
            ),
        )
        for galae, note in rows
    ]


async def deadline_items(
    session: AsyncSession, today: date, user_id: UUID | None = None
) -> list[tuple[UUID, DigestItem]]:
    """② 판단 시점 임박(D-7 이내)·도래(D-day) — 규칙 행 없이 judge_end 직접 스캔."""
    stmt = (
        select(Galae, Note)
        .join(Note, Galae.note_id == Note.id)
        .where(
            Galae.status == "open",
            Note.archived_at.is_(None),
            Galae.judge_end.is_not(None),
            Galae.judge_end >= today,
            Galae.judge_end <= today + timedelta(days=IMMINENT_DAYS),
        )
        .order_by(Galae.judge_end)
    )
    if user_id is not None:
        stmt = stmt.where(Note.user_id == user_id)
    rows = (await session.execute(stmt)).all()
    items: list[tuple[UUID, DigestItem]] = []
    for galae, note in rows:
        assert galae.judge_end is not None
        days_left = (galae.judge_end - today).days
        if days_left == 0:
            when = "오늘이 판단 시점입니다."
        else:
            when = f"판단 시점까지 {days_left}일 남았습니다."
        items.append(
            (
                note.user_id,
                DigestItem(
                    kind="deadline",
                    note_id=note.id,
                    galae_id=galae.id,
                    title=note.target_name,
                    reason=f"'{galae.question}' — {when}",
                    date=galae.judge_end,
                ),
            )
        )
    return items


async def interval_due_items(
    session: AsyncSession, now: datetime, user_id: UUID | None = None
) -> list[tuple[UUID, DigestItem, ReminderRule]]:
    """③ 정기 리마인드 — next_trigger_at 이 지난 interval 규칙.

    판단 시점이 하나도 없는 노트는 리마인드 대상이 아니다
    (development-plan §2.4 — '판단 시점을 정하면 리마인드가 시작됩니다').
    """
    stmt = (
        select(ReminderRule, Note)
        .join(Note, ReminderRule.note_id == Note.id)
        .where(
            ReminderRule.type == "interval",
            ReminderRule.next_trigger_at.is_not(None),
            ReminderRule.next_trigger_at <= now,
            Note.archived_at.is_(None),
            Note.id.in_(select(Galae.note_id).where(Galae.judge_end.is_not(None))),
        )
        .order_by(ReminderRule.next_trigger_at)
    )
    if user_id is not None:
        stmt = stmt.where(Note.user_id == user_id)
    rows = (await session.execute(stmt)).all()
    return [
        (
            note.user_id,
            DigestItem(
                kind="interval",
                note_id=note.id,
                title=note.target_name,
                reason=f"마지막 검토 후 {rule.current_interval_weeks}주가 지났습니다.",
                date=now.date(),
            ),
            rule,
        )
        for rule, note in rows
    ]


async def unsent_auto_met_items(
    session: AsyncSession,
) -> list[tuple[UUID, DigestItem, Notification]]:
    """④ auto 조건 달성 — evaluate 가 만든 미소비(sent_at null) 행을 다이제스트로 소비한다."""
    rows = (
        await session.execute(
            select(Notification, Note)
            .join(Note, Notification.note_id == Note.id)
            .where(
                Notification.kind == "auto_condition_met",
                Notification.sent_at.is_(None),
                Note.archived_at.is_(None),
            )
            .order_by(Notification.scheduled_for)
        )
    ).all()
    if not rows:
        return []
    scenario_ids = [
        UUID(str(n.payload["scenario_id"])) for n, _ in rows if n.payload.get("scenario_id")
    ]
    names = {
        row.id: row.name
        for row in (
            await session.execute(
                select(Scenario.id, Scenario.name).where(Scenario.id.in_(scenario_ids))
            )
        ).all()
    }
    items: list[tuple[UUID, DigestItem, Notification]] = []
    for notification, note in rows:
        scenario_id = notification.payload.get("scenario_id")
        name = names.get(UUID(str(scenario_id))) if scenario_id else None
        met_at_raw = notification.payload.get("met_at")
        met_at = date.fromisoformat(str(met_at_raw)) if met_at_raw else None
        reason = "설정한 확인 조건에 닿았습니다."
        if name:
            reason = f"'{name}' — {reason}"
        assert notification.note_id is not None
        items.append(
            (
                notification.user_id,
                DigestItem(
                    kind="auto_condition_met",
                    note_id=notification.note_id,
                    galae_id=UUID(str(notification.payload["galae_id"]))
                    if notification.payload.get("galae_id")
                    else None,
                    title=note.target_name,
                    reason=reason,
                    date=met_at,
                ),
                notification,
            )
        )
    return items


# ── 감쇠·하루 1건 ───────────────────────────────────────────────────────────


async def _last_digest_with_note(
    session: AsyncSession, user_id: UUID, note_id: UUID
) -> Notification | None:
    """그 노트가 항목으로 담겼던 가장 최근 다이제스트."""
    rows = await session.scalars(
        select(Notification)
        .where(Notification.user_id == user_id, Notification.kind == "reminder_digest")
        .order_by(Notification.scheduled_for.desc())
        .limit(50)
    )
    for notification in rows:
        items = notification.payload.get("items", [])
        if any(i.get("note_id") == str(note_id) for i in items):
            return notification
    return None


async def _apply_decay(
    session: AsyncSession, user_id: UUID, rule: ReminderRule, now: datetime
) -> None:
    """미열람 감쇠 — 직전 다이제스트가 안 열렸으면 주기 2배(최대 12주), 열렸으면 2주 리셋."""
    previous = await _last_digest_with_note(session, user_id, rule.note_id)
    if previous is not None:
        if previous.opened_at is None:
            rule.consecutive_unopened += 1
            rule.current_interval_weeks = min(rule.current_interval_weeks * 2, MAX_INTERVAL_WEEKS)
        else:
            rule.consecutive_unopened = 0
            rule.current_interval_weeks = DEFAULT_INTERVAL_WEEKS
    rule.next_trigger_at = now + timedelta(weeks=rule.current_interval_weeks)


async def _already_sent_today(session: AsyncSession, user_id: UUID, today: date) -> bool:
    start = datetime(today.year, today.month, today.day, tzinfo=UTC)
    found = await session.scalar(
        select(Notification.id)
        .where(
            Notification.user_id == user_id,
            Notification.kind == "reminder_digest",
            Notification.scheduled_for >= start,
            Notification.scheduled_for < start + timedelta(days=1),
        )
        .limit(1)
    )
    return found is not None


# ── 잡 본체 ─────────────────────────────────────────────────────────────────


async def run_daily_digest(
    session: AsyncSession, now: datetime | None = None
) -> list[Notification]:
    """사용자별로 모아 하루 1건. 만든 reminder_digest 행 목록을 반환한다(멱등)."""
    now = now or datetime.now(UTC)
    today = now.date()

    pending = await pending_judgment_items(session)
    deadline = await deadline_items(session, today)
    interval = await interval_due_items(session, now)
    auto_met = await unsent_auto_met_items(session)

    per_user: dict[UUID, list[DigestItem]] = {}
    seen: set[tuple[UUID, UUID]] = set()
    interval_rules: dict[UUID, list[ReminderRule]] = {}
    consumed_by_user: dict[UUID, list[Notification]] = {}

    def _add(user_id: UUID, item: DigestItem) -> bool:
        key = (user_id, item.note_id)
        if key in seen:
            return False  # 같은 노트는 최고 우선순위 하나만
        seen.add(key)
        per_user.setdefault(user_id, []).append(item)
        return True

    for user_id, item in pending:
        _add(user_id, item)
    for user_id, item in deadline:
        _add(user_id, item)
    for user_id, item, rule in interval:
        if _add(user_id, item):
            interval_rules.setdefault(user_id, []).append(rule)
    for user_id, item, source in auto_met:
        _add(user_id, item)
        # 상위 우선순위 항목이 같은 노트를 이미 다뤄도, 오늘 다이제스트로 소비된 것이다
        consumed_by_user.setdefault(user_id, []).append(source)

    created: list[Notification] = []
    for user_id, items in per_user.items():
        if await _already_sent_today(session, user_id, today):
            logger.info("오늘 다이제스트가 이미 있다 — 건너뜀 (user=%s)", user_id)
            continue
        for rule in interval_rules.get(user_id, []):
            await _apply_decay(session, user_id, rule, now)
        notification = Notification(
            user_id=user_id,
            note_id=items[0].note_id,  # 최고 우선순위 항목의 노트 — 상세 3단의 기준
            kind="reminder_digest",
            payload={"items": [i.to_payload() for i in items]},
            channel="in_app",
            scheduled_for=now,
            sent_at=now,  # 인앱에 노출 가능해진 시각 — 발송 개념은 없다
        )
        session.add(notification)
        for source in consumed_by_user.get(user_id, []):
            source.sent_at = source.sent_at or now  # 오늘 다이제스트에 담김 표시
        created.append(notification)
        logger.info(
            "digest 적재 (user=%s, 항목 %d건: %s)",
            user_id,
            len(items),
            ", ".join(i.kind for i in items),
        )

    await session.commit()
    logger.info("digest 완료: 대상 사용자 %d명, 다이제스트 %d건", len(per_user), len(created))
    return created
