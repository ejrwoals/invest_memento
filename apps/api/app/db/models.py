"""SQLAlchemy 2.0 매핑 — 마이그레이션 DDL의 반영일 뿐, 스키마를 생성하지 않는다.

정본은 supabase/migrations/002~005 이고, 필드명·타입은 그 DDL과 1:1 이다
(01-db-schema §8). CHECK·트리거·RLS 는 DB에만 있다 — 여기 반복하지 않는다.
M2 범위: instruments, notes, galae, scenarios, probability_entries, premises, watches.
"""

import uuid as uuid_pkg
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, SmallInteger, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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


class Premise(Base):
    """005_premises_reviews.sql — 근거 항목. 노트에 붙는다 — 갈래가 아니다."""

    __tablename__ = "premises"

    id: Mapped[uuid_pkg.UUID] = mapped_column(primary_key=True, default=uuid_pkg.uuid4)
    note_id: Mapped[uuid_pkg.UUID] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    statement: Mapped[str]
    position: Mapped[int] = mapped_column(default=0)
    quoted_from: Mapped[uuid_pkg.UUID | None]  # conversation_messages 참조 — M2에선 매핑 안 함
    linked_watch_id: Mapped[uuid_pkg.UUID | None] = mapped_column(
        ForeignKey("watches.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    note: Mapped[Note] = relationship(back_populates="premises")
