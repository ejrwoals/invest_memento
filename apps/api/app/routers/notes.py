"""노트 CRUD + 갈래 확률 갱신 — HTTP 계층은 얇게, 규칙은 domain 이 정본.

모든 엔드포인트는 RequireUser 로 보호되고, 모든 쿼리는 user_id 로 스코핑한다.
남의 리소스는 403이 아니라 404 — 존재 여부 자체를 흘리지 않는다 (02-backend §3).
확률 쓰기 경로는 PATCH /galae/{id}/probabilities 하나뿐이다 — 개별 시나리오
확률 수정 API는 만들지 않는다 (02-backend §4 "만들지 않는 엔드포인트").
"""

import zlib
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import RequireUser
from app.db import SessionDep
from app.db.models import (
    AutoConditionEdit,
    ContentBlock,
    Conversation,
    ConversationMessage,
    Galae,
    Instrument,
    Note,
    Premise,
    ProbabilityEntry,
    ReminderRule,
    Scenario,
    SeriesCatalogEntry,
    Watch,
)
from app.domain.probability import ScenarioProb, redistribute
from app.domain.validation import Issue, NoteDraft, ScenarioDraft, Severity, validate_note
from app.reminders.digest import DEFAULT_INTERVAL_WEEKS
from app.series.catalog import ensure_equity_series

router = APIRouter()

RESIDUAL_NAME = "그 외 예상 못한 전개"

# 색을 고르지 않고 저장하면 대상 이름에서 결정적으로 배정한다 — 같은 대상은 늘 같은 색
_PALETTE = ("#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0891b2")


# ── 응답 스키마 ──────────────────────────────────────────────────────────────


class FixOut(BaseModel):
    label: str
    action: str


class IssueOut(BaseModel):
    code: str
    severity: Severity
    field: str
    message: str
    fix: FixOut | None = None

    @classmethod
    def from_issue(cls, issue: Issue) -> "IssueOut":
        fix = FixOut(label=issue.fix.label, action=issue.fix.action) if issue.fix else None
        return cls(
            code=issue.code,
            severity=issue.severity,
            field=issue.field,
            message=issue.message,
            fix=fix,
        )


class NoteSummary(BaseModel):
    id: UUID
    target_name: str
    thesis_summary: str
    color: str
    is_complete: bool  # 판단 시점 있는 갈래가 하나 이상 — 저장하지 않고 여기서 계산한다
    next_judge_end: date | None  # 열린 갈래의 가장 가까운 판단 시점
    galae_count: int


class ScenarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    trigger_conditions: str | None
    position: int
    is_residual: bool
    status: str
    status_reason: str | None
    probability: int | None
    resolution_type: str
    series_provider: str | None
    series_code: str | None
    series_label: str | None
    comparator: str | None
    target_value: Decimal | None
    target_low: Decimal | None
    target_high: Decimal | None
    baseline_date: date | None
    auto_status: str | None
    met_at: date | None
    progress: float | None
    marked: str | None
    marked_at: datetime | None


class GalaeOut(BaseModel):
    id: UUID
    question: str
    judge_kind: str | None
    judge_start: date | None
    judge_end: date | None
    status: str
    position: int
    scenarios: list[ScenarioOut]


class PremiseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    statement: str
    position: int
    quoted_from: UUID | None
    linked_watch_id: UUID | None


class WatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    code: str
    label: str
    created_at: datetime


class NoteDetail(BaseModel):
    id: UUID
    target_type: str
    target_symbol: str | None
    target_name: str
    thesis_summary: str
    thesis_detail: str | None
    color: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    is_complete: bool
    galae: list[GalaeOut]
    premises: list[PremiseOut]
    watches: list[WatchOut]


class QuoteIn(BaseModel):
    """대표 인용 — 대화에서 온 사용자 발화. quoted_from 이 없으면 AI 저작으로 저장한다."""

    text: str
    quoted_from: UUID | None = None
    derived: bool = False


class NoteCreateBody(NoteDraft):
    """POST /notes 본문 — NoteDraft + 대화 연결(선택).

    conversation_id 가 있으면 같은 트랜잭션에서 conversation 을 노트에 붙이고
    (note_id 연결 + status='attached'), 대표 인용·가설을 content_blocks 로 저장한다.
    """

    conversation_id: UUID | None = None
    quote: QuoteIn | None = None


class ProbabilityChange(BaseModel):
    scenario_id: UUID
    value: int = Field(ge=0, le=100)


class ProbabilitiesPatch(BaseModel):
    changed: ProbabilityChange
    locked_ids: list[UUID] = Field(default_factory=list)
    reason: str | None = None  # 무엇을 보고 바꿨는지 (선택) — 움직인 시나리오의 이력에 남긴다


class ScenarioProbabilityOut(BaseModel):
    scenario_id: UUID
    value: int


class GalaeProbabilitiesOut(BaseModel):
    galae_id: UUID
    probabilities: list[ScenarioProbabilityOut]


# ── 순수 헬퍼 — DB 없이 테스트한다 ──────────────────────────────────────────


def ensure_residual(scenarios: Sequence[ScenarioDraft]) -> list[ScenarioDraft]:
    """갈래에 residual(`그 외 예상 못한 전개`)이 없으면 끝에 하나 추가한다.

    residual 생성은 갈래 생성 코드의 책임이다 — DB partial unique index 가
    "정확히 1개"를 보강한다 (01-db-schema §4.3).
    """
    if any(s.is_residual for s in scenarios):
        return list(scenarios)
    residual = ScenarioDraft(name=RESIDUAL_NAME, is_residual=True, resolution_type="complement")
    return [*scenarios, residual]


def pick_color(draft: NoteDraft) -> str:
    if draft.color:
        return draft.color
    return _PALETTE[zlib.crc32(draft.target_name.encode()) % len(_PALETTE)]


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def targets_complete(
    comparator: str | None,
    target_value: Decimal | None,
    target_low: Decimal | None,
    target_high: Decimal | None,
    baseline_date: date | None,
) -> bool:
    """comparator 별 목표값 완비 — gte/lte 는 목표값, between 은 상·하한,
    change_pct 는 기준일과 변화율까지 요구한다(없으면 판정 불능)."""
    if comparator == "between":
        return target_low is not None and target_high is not None
    if comparator == "change_pct":
        return baseline_date is not None and target_value is not None
    return target_value is not None


def check_auto_conditions(draft: NoteDraft) -> None:
    """auto 시나리오의 조건 완비 검사 — DB CHECK(01-db-schema §3.4)에 걸리기 전에
    완성된 문장으로 알려준다."""
    for s in (s for g in draft.galae for s in g.scenarios):
        if s.resolution_type != "auto":
            continue
        ok = (
            s.series_provider is not None
            and s.series_code is not None
            and s.comparator is not None
            and targets_complete(
                s.comparator, s.target_value, s.target_low, s.target_high, s.baseline_date
            )
        )
        if not ok:
            raise _error(
                422,
                "AUTO_CONDITION_INCOMPLETE",
                f"'{s.name}' 시나리오가 자동 판정인데 계열·비교 조건이 완성되지 않았습니다. "
                "조건을 채우거나 직접 표시(manual)로 저장해 주세요.",
            )


def _is_complete(galae: Sequence[Galae]) -> bool:
    return any(g.judge_end is not None for g in galae)


def _next_judge_end(galae: Sequence[Galae]) -> date | None:
    dues = [g.judge_end for g in galae if g.status == "open" and g.judge_end is not None]
    return min(dues) if dues else None


def _note_detail(note: Note) -> NoteDetail:
    return NoteDetail(
        id=note.id,
        target_type=note.target_type,
        target_symbol=note.target_symbol,
        target_name=note.target_name,
        thesis_summary=note.thesis_summary,
        thesis_detail=note.thesis_detail,
        color=note.color,
        archived_at=note.archived_at,
        created_at=note.created_at,
        updated_at=note.updated_at,
        is_complete=_is_complete(note.galae),
        galae=[
            GalaeOut(
                id=g.id,
                question=g.question,
                judge_kind=g.judge_kind,
                judge_start=g.judge_start,
                judge_end=g.judge_end,
                status=g.status,
                position=g.position,
                scenarios=[ScenarioOut.model_validate(s) for s in g.scenarios],
            )
            for g in note.galae
        ],
        premises=[PremiseOut.model_validate(p) for p in note.premises],
        watches=[WatchOut.model_validate(w) for w in note.watches],
    )


def _scenario_row(draft: ScenarioDraft, position: int) -> Scenario:
    is_auto = draft.resolution_type == "auto"
    return Scenario(
        name=draft.name,
        description=draft.description,
        trigger_conditions=draft.trigger_conditions,
        position=position,
        is_residual=draft.is_residual,
        resolution_type=draft.resolution_type,
        probability=None,  # 저장 시 확률은 전부 null — 배분은 갈래 단위 API에서만 (§3.1)
        series_provider=draft.series_provider if is_auto else None,
        series_code=draft.series_code if is_auto else None,
        series_label=draft.series_label if is_auto else None,
        comparator=draft.comparator if is_auto else None,
        target_value=draft.target_value if is_auto else None,
        target_low=draft.target_low if is_auto else None,
        target_high=draft.target_high if is_auto else None,
        baseline_date=draft.baseline_date if is_auto else None,
    )


# ── 엔드포인트 ──────────────────────────────────────────────────────────────


@router.post("/notes/validate")
def validate(draft: NoteDraft, user: RequireUser) -> list[IssueOut]:
    """검증기만 실행한다 — 저장하지 않는다. 미리보기 진입 시 클라이언트가 호출."""
    return [IssueOut.from_issue(i) for i in validate_note(draft)]


@router.post("/notes", status_code=201)
async def create_note(draft: NoteCreateBody, user: RequireUser, session: SessionDep) -> NoteDetail:
    issues = validate_note(draft)
    blocking = [i for i in issues if i.severity == "blocking"]
    if blocking:
        # blocking(NO_TARGET·NO_THESIS)만 저장을 막는다 — 나머지는 전부 저장된다 (§3.1)
        raise HTTPException(
            status_code=422,
            detail=[IssueOut.from_issue(i).model_dump() for i in blocking],
        )
    check_auto_conditions(draft)
    # 대화에서 이미 auto 조건이 올 수 있다(2단계 폼 전) — kis 종목이면 계열을 등록해
    # 수집 배치의 series_snapshots FK 가 성립하게 한다 (05 §3.1)
    for s in (s for g in draft.galae for s in g.scenarios):
        if s.resolution_type == "auto" and s.series_provider and s.series_code:
            await ensure_equity_series(session, s.series_provider, s.series_code)
    if draft.target_type is None:
        raise _error(
            422, "NO_TARGET_TYPE", "대상 유형(ticker·asset·theme)이 비어 있어 저장할 수 없습니다."
        )
    if draft.target_symbol is not None:
        known = await session.scalar(
            select(Instrument.symbol).where(Instrument.symbol == draft.target_symbol)
        )
        if known is None:
            raise _error(
                422,
                "UNKNOWN_SYMBOL",
                f"등록되지 않은 심볼입니다: {draft.target_symbol}. "
                "instruments 카탈로그에 먼저 추가해야 합니다.",
            )

    # 대화 연결(선택) — 같은 트랜잭션에서 attach 한다. 남의 대화는 404.
    conversation: Conversation | None = None
    valid_message_ids: set[UUID] = set()
    if draft.conversation_id is not None:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.id == draft.conversation_id,
                Conversation.user_id == UUID(user.id),
            )
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        if conversation.status != "draft" or conversation.note_id is not None:
            raise _error(409, "CONVERSATION_NOT_DRAFT", "이미 노트로 저장된 대화입니다.")
        valid_message_ids = set(
            (
                await session.scalars(
                    select(ConversationMessage.id).where(
                        ConversationMessage.conversation_id == conversation.id
                    )
                )
            ).all()
        )

    def _quoted_from(raw: str | UUID | None) -> UUID | None:
        # 인용 출처는 연결된 대화의 실제 메시지여야 한다 — 아니면 강등(None)
        if raw is None:
            return None
        value = raw if isinstance(raw, UUID) else UUID(raw)
        return value if value in valid_message_ids else None

    note = Note(
        user_id=UUID(user.id),
        target_type=draft.target_type,
        target_symbol=draft.target_symbol,
        target_name=draft.target_name,
        thesis_summary=draft.thesis_summary,
        thesis_detail=draft.thesis_detail,
        color=pick_color(draft),
    )
    for gpos, g in enumerate(draft.galae):
        galae = Galae(
            question=g.question,
            judge_kind=g.judge_kind,
            judge_start=g.judge_start,
            judge_end=g.judge_end,
            position=gpos,
        )
        galae.scenarios = [
            _scenario_row(s, spos) for spos, s in enumerate(ensure_residual(g.scenarios))
        ]
        note.galae.append(galae)
    note.premises = [
        Premise(
            statement=p.statement,
            position=ppos,
            quoted_from=_quoted_from(p.quoted_from),
        )
        for ppos, p in enumerate(draft.premises)
    ]
    session.add(note)
    await session.flush()  # note.id 확보 — conversation 연결·content_blocks 에 필요

    # 정기 리마인드 규칙 — 노트당 interval 1개, 기본 2주 (01-db-schema §3.8).
    # 갈래 시점 기반(임박·도래)은 규칙 행이 없다 — 일일 잡이 judge_end 를 직접 스캔한다.
    session.add(
        ReminderRule(
            note_id=note.id,
            type="interval",
            next_trigger_at=datetime.now(UTC) + timedelta(weeks=DEFAULT_INTERVAL_WEEKS),
        )
    )

    if conversation is not None:
        conversation.note_id = note.id
        conversation.status = "attached"

    # 가설·대표 인용을 본문 블록으로 — 대조 실패(quoted_from 무효)면 AI 저작으로 강등
    position = 0
    if draft.thesis_detail and draft.thesis_detail.strip():
        session.add(
            ContentBlock(
                note_id=note.id,
                section="thesis",
                position=position,
                content=draft.thesis_detail,
                authorship="ai",
            )
        )
        position += 1
    if draft.quote is not None and draft.quote.text.strip():
        quoted_from = _quoted_from(draft.quote.quoted_from)
        session.add(
            ContentBlock(
                note_id=note.id,
                section="thesis_quote",
                position=position,
                content=draft.quote.text,
                authorship="user" if quoted_from else "ai",
                quoted_from=quoted_from,
                derived=draft.quote.derived,
            )
        )

    await session.commit()  # 단일 트랜잭션 — 부분 저장은 없다 (02-backend §9)
    saved = await _load_note(session, UUID(user.id), note.id)
    assert saved is not None
    return _note_detail(saved)


@router.get("/notes")
async def list_notes(user: RequireUser, session: SessionDep) -> list[NoteSummary]:
    notes = (
        await session.scalars(
            select(Note)
            .where(Note.user_id == UUID(user.id))
            .options(selectinload(Note.galae))
            .order_by(Note.created_at.desc())
        )
    ).all()
    return [
        NoteSummary(
            id=n.id,
            target_name=n.target_name,
            thesis_summary=n.thesis_summary,
            color=n.color,
            is_complete=_is_complete(n.galae),
            next_judge_end=_next_judge_end(n.galae),
            galae_count=len(n.galae),
        )
        for n in notes
    ]


async def _load_note(session: AsyncSession, user_id: UUID, note_id: UUID) -> Note | None:
    result = await session.scalars(
        select(Note)
        .where(Note.id == note_id, Note.user_id == user_id)
        .options(
            selectinload(Note.galae).selectinload(Galae.scenarios),
            selectinload(Note.premises),
            selectinload(Note.watches),
        )
    )
    return result.first()


@router.get("/notes/{note_id}")
async def get_note(note_id: UUID, user: RequireUser, session: SessionDep) -> NoteDetail:
    note = await _load_note(session, UUID(user.id), note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return _note_detail(note)


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: UUID, user: RequireUser, session: SessionDep) -> None:
    owned = await session.scalar(
        select(Note.id).where(Note.id == note_id, Note.user_id == UUID(user.id))
    )
    if owned is None:
        raise HTTPException(status_code=404, detail="note not found")
    # residual 삭제 보호 트리거(01-db-schema §4.3)는 노트 cascade 삭제에도 발화하므로,
    # 같은 트랜잭션 안에서 is_residual 을 접어 무장해제한 뒤 지운다.
    await session.execute(
        update(Scenario)
        .values(is_residual=False)
        .where(Scenario.galae_id.in_(select(Galae.id).where(Galae.note_id == note_id)))
    )
    await session.execute(delete(Note).where(Note.id == note_id))
    await session.commit()


@router.patch("/galae/{galae_id}/probabilities")
async def patch_probabilities(
    galae_id: UUID, body: ProbabilitiesPatch, user: RequireUser, session: SessionDep
) -> GalaeProbabilitiesOut:
    galae = await session.scalar(
        select(Galae)
        .join(Note)
        .where(Galae.id == galae_id, Note.user_id == UUID(user.id))
        .options(selectinload(Galae.scenarios))
    )
    if galae is None:
        raise HTTPException(status_code=404, detail="galae not found")

    changed_id = str(body.changed.scenario_id)
    if changed_id not in {str(s.id) for s in galae.scenarios}:
        # 갈래 밖의 시나리오 — 존재 여부를 흘리지 않고 404
        raise HTTPException(status_code=404, detail="scenario not found")

    current = [
        ScenarioProb(id=str(s.id), probability=s.probability, is_residual=s.is_residual)
        for s in galae.scenarios
    ]
    try:
        result = redistribute(
            current, changed_id, body.changed.value, {str(i) for i in body.locked_ids}
        )
    except ValueError as e:
        raise _error(422, "PROBABILITY_INPUT_INVALID", str(e)) from e
    if result is None:
        raise _error(
            422,
            "SINGLE_SCENARIO",
            "답이 하나뿐인 갈래에는 확률을 배분하지 않습니다. 시나리오를 먼저 추가해 주세요.",
        )

    # 한 트랜잭션: scenarios.probability 전체 UPDATE + 바뀐 값만 probability_entries INSERT.
    # reason 은 사용자가 직접 움직인 시나리오의 이력에만 남긴다 — 나머지는 기계적 재분배다.
    for s in galae.scenarios:
        new_value = result[str(s.id)]
        if s.probability != new_value:
            session.add(
                ProbabilityEntry(
                    scenario_id=s.id,
                    value=new_value,
                    reason=body.reason if str(s.id) == changed_id else None,
                )
            )
        s.probability = new_value
    await session.commit()

    return GalaeProbabilitiesOut(
        galae_id=galae.id,
        probabilities=[
            ScenarioProbabilityOut(scenario_id=s.id, value=result[str(s.id)])
            for s in galae.scenarios
        ],
    )


# ── 2단계 폼 — auto 조건 설정·수정과 지켜보는 수치 (ux §3.3) ─────────────────


class ResolutionPatch(BaseModel):
    """auto 조건 전체를 한 번에 받는다 — 한 답의 auto 조건은 하나뿐이다 (ux §3.3)."""

    series_provider: str
    series_code: str
    series_label: str
    comparator: Literal["gte", "lte", "between", "change_pct"]
    target_value: Decimal | None = None
    target_low: Decimal | None = None
    target_high: Decimal | None = None
    baseline_date: date | None = None
    reason: str | None = None  # 왜 조건을 바꿨는지 (선택) — 수정 이력에만 남긴다


# 조건을 이루는 칼럼들 — 수정 이력은 이 필드 단위로 남는다 (01-db-schema, 004 DDL)
_CONDITION_FIELDS = (
    "series_provider",
    "series_code",
    "series_label",
    "comparator",
    "target_value",
    "target_low",
    "target_high",
    "baseline_date",
)


def _condition_text(value: str | Decimal | date | None) -> str | None:
    """auto_condition_edits 는 값을 text 로 평탄화해 담는다.

    Numeric(18,4) 칼럼은 95000.0000 으로 돌아오므로 Decimal 은 뒤 0을 걷어낸다 —
    이력은 사람이 읽는 값이다."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


async def _require_known_series(session: AsyncSession, provider: str, code: str) -> None:
    # 개별 주식(kis)은 시드되지 않는다 — 처음 참조되는 순간 kind='equity' 로 등록한다
    await ensure_equity_series(session, provider, code)
    known = await session.scalar(
        select(SeriesCatalogEntry.code).where(
            SeriesCatalogEntry.provider == provider, SeriesCatalogEntry.code == code
        )
    )
    if known is None:
        raise _error(
            422,
            "UNKNOWN_SERIES",
            f"등록되지 않은 계열입니다: {provider}/{code}. 검색에서 고른 계열만 담을 수 있습니다.",
        )


@router.patch("/scenarios/{scenario_id}/resolution")
async def patch_resolution(
    scenario_id: UUID, body: ResolutionPatch, user: RequireUser, session: SessionDep
) -> ScenarioOut:
    scenario = await session.scalar(
        select(Scenario)
        .join(Galae)
        .join(Note)
        .where(Scenario.id == scenario_id, Note.user_id == UUID(user.id))
    )
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    if scenario.resolution_type != "auto":
        # complement·residual 은 조건을 가질 수 없고(ux §3.3), manual 은 수치 질문이 아니다
        raise _error(
            409,
            "RESOLUTION_NOT_AUTO",
            "자동 확인으로 저장된 답이 아니라 조건을 둘 수 없습니다. "
            "판단 시점이 오면 직접 표시하는 답입니다.",
        )
    if not targets_complete(
        body.comparator, body.target_value, body.target_low, body.target_high, body.baseline_date
    ):
        raise _error(
            422,
            "AUTO_CONDITION_INCOMPLETE",
            "비교 방식에 맞는 목표값이 비어 있어 저장할 수 없습니다.",
        )
    await _require_known_series(session, body.series_provider, body.series_code)

    # 기존 조건이 있으면 수정이다 — 달라진 필드마다 이력을 남긴다 (02-backend §4)
    is_edit = scenario.series_provider is not None
    changed = False
    for field in _CONDITION_FIELDS:
        old = getattr(scenario, field)
        new = getattr(body, field)
        if old == new:
            continue
        changed = True
        if is_edit:
            session.add(
                AutoConditionEdit(
                    scenario_id=scenario.id,
                    field=field,
                    from_value=_condition_text(old),
                    to_value=_condition_text(new),
                    reason=body.reason,
                )
            )
        setattr(scenario, field, new)
    if changed:
        # 조건이 달라졌으니 평가 캐시는 낡았다 — 다음 배치가 다시 계산한다 (05 §5)
        scenario.auto_status = None
        scenario.met_at = None
        scenario.progress = None
    await session.commit()
    return ScenarioOut.model_validate(scenario)


class WatchCreate(BaseModel):
    provider: str
    code: str
    label: str


@router.post("/notes/{note_id}/watches", status_code=201)
async def create_watch(
    note_id: UUID, body: WatchCreate, user: RequireUser, session: SessionDep
) -> WatchOut:
    owned = await session.scalar(
        select(Note.id).where(Note.id == note_id, Note.user_id == UUID(user.id))
    )
    if owned is None:
        raise HTTPException(status_code=404, detail="note not found")
    await _require_known_series(session, body.provider, body.code)
    watch = Watch(note_id=note_id, provider=body.provider, code=body.code, label=body.label)
    session.add(watch)
    await session.commit()
    return WatchOut.model_validate(watch)


@router.delete("/watches/{watch_id}", status_code=204)
async def delete_watch(watch_id: UUID, user: RequireUser, session: SessionDep) -> None:
    watch = await session.scalar(
        select(Watch).join(Note).where(Watch.id == watch_id, Note.user_id == UUID(user.id))
    )
    if watch is None:
        raise HTTPException(status_code=404, detail="watch not found")
    await session.delete(watch)
    await session.commit()
