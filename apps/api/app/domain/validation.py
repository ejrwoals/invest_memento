"""노트 검증기 — AI에게 묻지 않고 결정론적 코드가 검사한다.

세 층 중 이 모듈은 규칙층(3층)과 구조층(1층, NoteDraft 파싱)을 담당한다.
규칙은 각각 순수 함수 하나이고, blocking 은 NO_TARGET·NO_THESIS 둘뿐이다 —
검증기의 목적은 막는 것이 아니라 무엇이 왜 비었는지 말해주는 것이다.
근거: docs/development-plan.md §3.1 "규칙은 심각도로 나눈다", docs/dev/02-backend.md §5.

확률 합은 여기서 검사하지 않는다 — probability.redistribute 가 어긋날 수 없게 만든다.
주기적 재검사도 하지 않는다 — 시간 경과에 따른 상태 변화는 상태 전이(02-backend §8)의 일이다.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["blocking", "ask", "incomplete", "notice"]


@dataclass(frozen=True)
class Fix:
    """"지금 추가하기" 같은 바로가기. action 은 클라이언트가 해석하는 식별자다."""

    label: str
    action: str


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity
    field: str
    # UI가 그대로 쓸 완성된 문장 — "필수 항목 누락" 같은 코드 번역이 아니다.
    message: str
    fix: Fix | None = None


# --- 입력 모델 — 필드명은 DB 스키마(01-db-schema §3.3~3.5)와 정합 ---


class ScenarioDraft(BaseModel):
    name: str = ""
    description: str | None = None
    trigger_conditions: str | None = None
    is_residual: bool = False
    resolution_type: Literal["auto", "manual", "complement"] = "manual"
    probability: int | None = None
    # auto 전용 — 조건은 하나뿐이다. 저장 시 DB CHECK(01-db-schema §3.4)와 정합해야 한다
    series_provider: Literal["fred", "ecos", "kis"] | None = None
    series_code: str | None = None
    series_label: str | None = None
    comparator: Literal["gte", "lte", "between", "change_pct"] | None = None
    target_value: Decimal | None = None
    target_low: Decimal | None = None
    target_high: Decimal | None = None
    baseline_date: date | None = None


class GalaeDraft(BaseModel):
    question: str = ""
    judge_kind: Literal["date", "range"] | None = None
    judge_start: date | None = None
    judge_end: date | None = None  # 판단 시점은 갈래에 하나뿐이다 — 시나리오에는 날짜가 없다
    scenarios: list[ScenarioDraft] = Field(default_factory=list)


class PremiseDraft(BaseModel):
    statement: str  # 사용자가 말한 그대로. 다듬지 않는다
    quoted_from: str | None = None


class NoteDraft(BaseModel):
    target_type: Literal["ticker", "asset", "theme"] | None = None
    target_symbol: str | None = None
    target_name: str = ""
    thesis_summary: str = ""
    thesis_detail: str | None = None
    color: str | None = None  # 홈 타임라인 식별색 — 비우면 저장 API가 팔레트에서 정한다
    galae: list[GalaeDraft] = Field(default_factory=list)
    premises: list[PremiseDraft] = Field(default_factory=list)


# --- 규칙 — 각각 순수 함수 하나. 노트를 받아 Issue | None 을 반환한다 ---

Rule = Callable[[NoteDraft, date], "Issue | None"]


def _no_target(draft: NoteDraft, today: date) -> Issue | None:
    if draft.target_name.strip():
        return None
    return Issue(
        code="NO_TARGET",
        severity="blocking",
        field="target_name",
        message=(
            "투자 대상이 정해지지 않았습니다. "
            "대상이 없으면 노트가 성립하지 않아 저장할 수 없습니다."
        ),
    )


def _no_thesis(draft: NoteDraft, today: date) -> Issue | None:
    if draft.thesis_summary.strip():
        return None
    return Issue(
        code="NO_THESIS",
        severity="blocking",
        field="thesis_summary",
        message="핵심 가설이 비어 있습니다. 가설이 없으면 노트가 성립하지 않아 저장할 수 없습니다.",
    )


def _no_deadline(draft: NoteDraft, today: date) -> Issue | None:
    # 노트의 완성 여부는 "판단 시점 있는 갈래가 하나 이상 존재"에서 파생된다
    # (01-db-schema §3.3). 하나라도 있으면 리마인드가 돌므로 노트 단위로는 통과다.
    if any(g.judge_end is not None for g in draft.galae):
        return None
    return Issue(
        code="NO_DEADLINE",
        severity="ask",
        field="galae.judge_end",
        message=(
            "판단 시점이 비어 있어 리마인드가 오지 않습니다. "
            "나중에 정하면 리마인드가 그대로 시작됩니다."
        ),
        fix=Fix(label="지금 정하기", action="set_deadline"),
    )


def _single_scenario(draft: NoteDraft, today: date) -> Issue | None:
    for i, g in enumerate(draft.galae):
        if len(g.scenarios) == 1:
            return Issue(
                code="SINGLE_SCENARIO",
                severity="ask",
                field=f"galae[{i}].scenarios",
                message=(
                    "대화에서 이 판단이 틀린 경우를 이야기하지 않아 "
                    "반대 시나리오를 비워 두었습니다. "
                    "답이 하나뿐이면 확률 배분이 열리지 않습니다."
                ),
                fix=Fix(label="지금 추가하기", action="add_scenario"),
            )
    return None


def _no_premise(draft: NoteDraft, today: date) -> Issue | None:
    if draft.premises:
        return None
    return Issue(
        code="NO_PREMISE",
        severity="ask",
        field="premises",
        message=(
            "이 판단의 전제가 남아 있지 않습니다. "
            "근거 항목이 없으면 회고 때 검증할 대상이 없어집니다."
        ),
        fix=Fix(label="지금 추가하기", action="add_premise"),
    )


def _no_galae_question(draft: NoteDraft, today: date) -> Issue | None:
    # 시나리오는 있는데 무엇을 놓고 갈리는지가 없다 — 건너뛰면 가설 문장을 질문으로 임시 표시.
    for i, g in enumerate(draft.galae):
        if g.scenarios and not g.question.strip():
            return Issue(
                code="NO_GALAE_QUESTION",
                severity="ask",
                field=f"galae[{i}].question",
                message=(
                    "시나리오들이 무엇을 놓고 갈리는지 질문이 비어 있습니다. "
                    "비워 두면 가설 문장을 질문으로 임시 표시합니다."
                ),
                fix=Fix(label="지금 적기", action="set_question"),
            )
    return None


def _no_auto_resolution(draft: NoteDraft, today: date) -> Issue | None:
    scenarios = [s for g in draft.galae for s in g.scenarios]
    if not scenarios or any(s.resolution_type == "auto" for s in scenarios):
        return None
    return Issue(
        code="NO_AUTO_RESOLUTION",
        severity="notice",
        field="galae",
        message=(
            "자동으로 확인되는 답이 없습니다. "
            "판단 시점이 오면 여쭙고, 결과는 직접 표시하시면 됩니다."
        ),
    )


def _deadline_in_past(draft: NoteDraft, today: date) -> Issue | None:
    # 작성 시점에 이미 지난 날짜 — 오타일 가능성. 당일은 도래이지 과거가 아니다.
    for i, g in enumerate(draft.galae):
        if g.judge_end is not None and g.judge_end < today:
            return Issue(
                code="DEADLINE_IN_PAST",
                severity="notice",
                field=f"galae[{i}].judge_end",
                message=(
                    "판단 시점이 이미 지난 날짜입니다. "
                    "잘못 적힌 날짜일 수 있어 확인이 필요합니다."
                ),
            )
    return None


RULES: tuple[Rule, ...] = (
    _no_target,
    _no_thesis,
    _no_deadline,
    _single_scenario,
    _no_premise,
    _no_galae_question,
    _no_auto_resolution,
    _deadline_in_past,
)


def validate_note(draft: NoteDraft, today: date | None = None) -> list[Issue]:
    """규칙층 전체 실행. today 는 DEADLINE_IN_PAST 판정 기준일 — 테스트에서 주입한다."""
    base = today if today is not None else date.today()
    return [issue for rule in RULES if (issue := rule(draft, base)) is not None]


def check_quoted_sources(draft: NoteDraft, conversation_texts: Sequence[str]) -> list[Issue]:
    """2층 — 출처 대조. 인용이라고 주장한 문장이 실제 대화에 있는지 찾는다.

    이번 마일스톤(M2) 범위 밖 — 대화 저장소가 붙는 시점에 구현한다.
    공백 정규화 후 부분 문자열 일치로 원본 대화에서 검색하고, 못 찾으면
    인용 표시를 떼어 authorship='ai' 로 강등한다 (02-backend §5).
    """
    return []
