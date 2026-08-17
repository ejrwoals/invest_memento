"""SQLAlchemy 2.0 매핑 — 마이그레이션 DDL의 반영일 뿐, 스키마를 생성하지 않는다.

정본은 supabase/migrations/002~008 이고, 필드명·타입은 그 DDL과 1:1 이다
(01-db-schema §8). CHECK·트리거·RLS 는 DB에만 있다 — 여기 반복하지 않는다.
M2 범위: instruments, notes, galae, scenarios, probability_entries, premises, watches.
M3 추가: conversations, conversation_messages, content_blocks (003 DDL).
conversation_messages 는 DB 트리거로 불변이다 — 코드는 INSERT 만 한다.
M4 추가: series_catalog (002), series_snapshots (008), notifications (006),
auto_condition_edits (004 — 2단계 폼의 조건 수정 이력).
M5 추가: reminder_rules (006 — 정기 리마인드 규칙).
"""

import uuid as uuid_pkg
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# 'date' 라는 이름의 칼럼(series_snapshots.date)이 있는 클래스에서는 어노테이션의
# `date` 가 칼럼 속성에 가려지므로 모듈 수준 별칭으로 참조한다.
DateOnly = date


class Base(DeclarativeBase):
    type_annotation_map = {
        str: Text(),
        datetime: DateTime(timezone=True),  # timestamptz
        uuid_pkg.UUID: Uuid(),
    }


class Instrument(Base):
    """002_catalog.sql — 티커 정규화 사전 (전역, user_id 없음)."""

    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    market: Mapped[str]
    currency: Mapped[str]
    kis_code: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SeriesCatalogEntry(Base):
    """002_catalog.sql — 계열 사전 (전역). 여기 없는 계열은 조건으로 설정할 수 없다."""

    __tablename__ = "series_catalog"

    provider: Mapped[str] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str]
    kind: Mapped[str]
    unit: Mapped[str | None]
    has_intraday: Mapped[bool] = mapped_column(default=False)
    # text[] — sqlite 테스트에서는 JSON 배열로 저장된다
    search_keywords: Mapped[list[str] | None] = mapped_column(
        JSON().with_variant(ARRAY(Text()), "postgresql")
    )


class SeriesSnapshot(Base):
    """008_series_ops.sql — 수치 스냅샷 (전역 캐시). 미마감 당일은 절대 없다."""

    __tablename__ = "series_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["provider", "code"], ["series_catalog.provider", "series_catalog.code"]
        ),
    )

    provider: Mapped[str] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(primary_key=True)
    date: Mapped[DateOnly] = mapped_column(primary_key=True)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))  # 거시 계열은 null
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    fetched_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Notification(Base):
    """006 + 011_in_app_channel.sql — 알림 행. Redis 없는 이벤트 큐이기도 하다 (05 §5.4).

    채널은 in_app 뿐이다 — 이메일 발송은 없다 (M5 범위 결정)."""

    __tablename__ = "notifications"

    id: Mapped[uuid_pkg.UUID] = mapped_column(primary_key=True, default=uuid_pkg.uuid4)
    user_id: Mapped[uuid_pkg.UUID]
    note_id: Mapped[uuid_pkg.UUID | None] = mapped_column(
        ForeignKey("notes.id", ondelete="CASCADE")
    )
    kind: Mapped[str]  # 'auto_condition_met' | 'judgment_due' | 'reminder_digest' ...
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=dict
    )
    channel: Mapped[str] = mapped_column(default="in_app")
    scheduled_for: Mapped[datetime]
    sent_at: Mapped[datetime | None]
    opened_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ReminderRule(Base):
    """006_research_reminders.sql — 리마인드 규칙. MVP 는 노트당 interval 1개.

    갈래 시점 기반(임박·도래)은 규칙 행이 없다 — 일일 다이제스트 잡이
    galae.judge_end 를 직접 스캔한다. consecutive_unopened 와 감쇠 상태는
    화면에 절대 노출하지 않는다 (P5).
    """

    __tablename__ = "reminder_rules"

    id: Mapped[uuid_pkg.UUID] = mapped_column(primary_key=True, default=uuid_pkg.uuid4)
    note_id: Mapped[uuid_pkg.UUID] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    type: Mapped[str]  # 'interval' | 'galae_deadline' | 'pending_judgment' | 'event_triggered'
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=dict
    )
    next_trigger_at: Mapped[datetime | None]
    consecutive_unopened: Mapped[int] = mapped_column(Integer, default=0)
    current_interval_weeks: Mapped[int] = mapped_column(Integer, default=2)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Note(Base):
    """003_notes_conversations.sql — 노트 축."""

    __tablename__ = "notes"

    id: Mapped[uuid_pkg.UUID] = mapped_column(primary_key=True, default=uuid_pkg.uuid4)
    user_id: Mapped[uuid_pkg.UUID]  # auth.users 참조 — auth 스키마는 매핑하지 않는다
    target_type: Mapped[str]
    target_symbol: Mapped[str | None] = mapped_column(ForeignKey("instruments.symbol"))
    target_name: Mapped[str]
    thesis_summary: Mapped[str]
    thesis_detail: Mapped[str | None]
    color: Mapped[str]
    archived_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())

    galae: Mapped[list["Galae"]] = relationship(
        back_populates="note", order_by="Galae.position", cascade="all, delete-orphan"
    )
    premises: Mapped[list["Premise"]] = relationship(
        back_populates="note", order_by="Premise.position", cascade="all, delete-orphan"
    )
    watches: Mapped[list["Watch"]] = relationship(
        back_populates="note", cascade="all, delete-orphan"
    )


class Galae(Base):
    """004_galae_scenarios.sql — 판단 시점은 갈래에 하나뿐이다."""

    __tablename__ = "galae"

    id: Mapped[uuid_pkg.UUID] = mapped_column(primary_key=True, default=uuid_pkg.uuid4)
    note_id: Mapped[uuid_pkg.UUID] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    question: Mapped[str]
    judge_kind: Mapped[str | None]
    judge_start: Mapped[date | None]
    judge_end: Mapped[date | None]
    status: Mapped[str] = mapped_column(default="open")
    position: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())

    note: Mapped[Note] = relationship(back_populates="galae")
    scenarios: Mapped[list["Scenario"]] = relationship(
        back_populates="galae", order_by="Scenario.position", cascade="all, delete-orphan"
    )


class Scenario(Base):
    """004_galae_scenarios.sql — 시나리오. 확률 현재값은 여기, 이력은 probability_entries."""

    __tablename__ = "scenarios"

    id: Mapped[uuid_pkg.UUID] = mapped_column(primary_key=True, default=uuid_pkg.uuid4)
    galae_id: Mapped[uuid_pkg.UUID] = mapped_column(ForeignKey("galae.id", ondelete="CASCADE"))
    name: Mapped[str]
    description: Mapped[str | None]
    trigger_conditions: Mapped[str | None]
    position: Mapped[int] = mapped_column(default=0)
    is_residual: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(default="active")
    status_reason: Mapped[str | None]
    probability: Mapped[int | None] = mapped_column(SmallInteger)
    resolution_type: Mapped[str]
    # auto 전용 — 조건은 하나뿐이다
    series_provider: Mapped[str | None]
    series_code: Mapped[str | None]
    series_label: Mapped[str | None]
    comparator: Mapped[str | None]
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    target_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    target_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    baseline_date: Mapped[date | None]
    auto_status: Mapped[str | None]
    met_at: Mapped[date | None]
    progress: Mapped[float | None]
    # manual 전용
    marked: Mapped[str | None]
    marked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())

    galae: Mapped[Galae] = relationship(back_populates="scenarios")
    probability_entries: Mapped[list["ProbabilityEntry"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )


class AutoConditionEdit(Base):
    """004_galae_scenarios.sql — auto 조건 사후 수정 이력. 값은 text 로 평탄화한다."""

    __tablename__ = "auto_condition_edits"

    id: Mapped[uuid_pkg.UUID] = mapped_column(primary_key=True, default=uuid_pkg.uuid4)
    scenario_id: Mapped[uuid_pkg.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE")
    )
    field: Mapped[str]
    from_value: Mapped[str | None]
    to_value: Mapped[str | None]
    reason: Mapped[str | None]
    edited_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProbabilityEntry(Base):
    """004_galae_scenarios.sql — 확률 이력. 출처 컬럼이 없다 — 전부 사용자의 판단이다."""

    __tablename__ = "probability_entries"

    id: Mapped[uuid_pkg.UUID] = mapped_column(primary_key=True, default=uuid_pkg.uuid4)
    scenario_id: Mapped[uuid_pkg.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE")
    )
    value: Mapped[int] = mapped_column(SmallInteger)
    reason: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    scenario: Mapped[Scenario] = relationship(back_populates="probability_entries")


class Watch(Base):
    """005_premises_reviews.sql — 지켜보는 수치. 판정하지 않는다."""

    __tablename__ = "watches"

    id: Mapped[uuid_pkg.UUID] = mapped_column(primary_key=True, default=uuid_pkg.uuid4)
    note_id: Mapped[uuid_pkg.UUID] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    provider: Mapped[str]
    code: Mapped[str]
    label: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    note: Mapped[Note] = relationship(back_populates="watches")


class Conversation(Base):
    """003_notes_conversations.sql — 대화는 노트보다 먼저 태어난다(draft 재개)."""

    __tablename__ = "conversations"

    id: Mapped[uuid_pkg.UUID] = mapped_column(primary_key=True, default=uuid_pkg.uuid4)
    user_id: Mapped[uuid_pkg.UUID]
    note_id: Mapped[uuid_pkg.UUID | None] = mapped_column(
        ForeignKey("notes.id", ondelete="SET NULL"), unique=True
    )
    status: Mapped[str] = mapped_column(default="draft")  # draft | attached | abandoned
    # 작성 중 실시간 패널 상태 (UX §3.2) — jsonb. sqlite 테스트에서는 JSON 으로 동작한다.
    draft_note: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())

    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation",
        order_by="ConversationMessage.seq",
        cascade="all, delete-orphan",
    )


class ConversationMessage(Base):
    """003_notes_conversations.sql — 원본 대화 불변(P2). UPDATE 불가, DELETE 는 GUC 필요."""

    __tablename__ = "conversation_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "seq"),)

    id: Mapped[uuid_pkg.UUID] = mapped_column(primary_key=True, default=uuid_pkg.uuid4)
    conversation_id: Mapped[uuid_pkg.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str]  # user | assistant
    content: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ContentBlock(Base):
    """003_notes_conversations.sql — 노트 본문 블록. user 저작만 [사용자] 표기."""

    __tablename__ = "content_blocks"

    id: Mapped[uuid_pkg.UUID] = mapped_column(primary_key=True, default=uuid_pkg.uuid4)
    note_id: Mapped[uuid_pkg.UUID] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    section: Mapped[str]  # thesis | thesis_quote | scenario | premise_intro | free
    position: Mapped[int] = mapped_column(default=0)
    content: Mapped[str]
    authorship: Mapped[str]  # ai | user
    quoted_from: Mapped[uuid_pkg.UUID | None] = mapped_column(
        ForeignKey("conversation_messages.id")
    )
    derived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Premise(Base):
    """005_premises_reviews.sql — 근거 항목. 노트에 붙는다 — 갈래가 아니다."""

    __tablename__ = "premises"

    id: Mapped[uuid_pkg.UUID] = mapped_column(primary_key=True, default=uuid_pkg.uuid4)
    note_id: Mapped[uuid_pkg.UUID] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    statement: Mapped[str]
    position: Mapped[int] = mapped_column(default=0)
    quoted_from: Mapped[uuid_pkg.UUID | None] = mapped_column(
        ForeignKey("conversation_messages.id")
    )
    linked_watch_id: Mapped[uuid_pkg.UUID | None] = mapped_column(
        ForeignKey("watches.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    note: Mapped[Note] = relationship(back_populates="premises")
