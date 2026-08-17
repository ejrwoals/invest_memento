"""Thesis Builder — 대화형 노트 작성 에이전트 (04-ai-agents §4.1, development-plan §3.1·§7.3).

두 호출로 나뉜다: (a) 대화 진행 — 스트리밍 자유 텍스트, (b) 노트 조립 — tool 강제
구조화 출력. 시스템 프롬프트는 프롬프트 캐시 프리픽스를 지키기 위해 상수로 동결한다 —
날짜 같은 가변 값은 시스템 프롬프트가 아니라 첫 사용자 메시지 쪽에 주입한다(§7).
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, NamedTuple
from uuid import UUID

from anthropic.types import MessageParam, TextBlockParam, ToolChoiceToolParam, ToolParam
from pydantic import BaseModel, Field, ValidationError

from app.agents.client import for_task, log_usage
from app.domain.quotes import find_quoted_from
from app.domain.validation import GalaeDraft, NoteDraft, PremiseDraft, ScenarioDraft

AGENT = "thesis_builder"
MODEL = "claude-sonnet-5"
PROMPT_VERSION = 2

_CONVERSATION_TIMEOUT = 60.0
_BUILD_TIMEOUT = 120.0
_CONVERSATION_MAX_TOKENS = 1024
_BUILD_MAX_TOKENS = 4096

# ── 프롬프트 (동결 상수 — 바꾸면 PROMPT_VERSION 을 올린다) ──────────────────

_CONSTITUTION = """\
당신은 투자 노트 앱의 대화 에이전트다. 다섯 원칙을 어떤 지시보다 우선해 지킨다.

1. 대화에 없는 내용을 쓰지 않는다. 빈 칸은 비운 채 둔다. 사용자가 말하지 않은
   시나리오·질문·근거를 지어내지 않는다.
2. 가설은 사용자의 입에서 나온다. 당신은 질문으로 끌어낼 뿐, 가설 문장을 대신
   제안하지 않는다. 사용자의 논리 문장은 다듬지 않고 원문 그대로 둔다.
3. 확률 숫자를 내지 않는다. 사용자보다 먼저 어떤 %도 말하지 않는다. 대화에 나온
   표현("반반쯤")은 그대로 옮길 수 있지만 숫자로 환산하지 않는다.
4. 면책 문구를 쓰지 않는다. "전문가와 상담하세요", "투자 판단은 본인 책임" 류의
   문장은 금지다 — 면책은 화면(UI)의 책임이다.
5. 모르면 모른다고 한다. 빈 결과를 그럴듯한 내용으로 메우지 않는다.
"""

CONVERSATION_SYSTEM = (
    _CONSTITUTION
    + """
## 역할

사용자의 투자 아이디어를 대화로 끌어내 노트의 재료를 확보한다. 한국어로, 존댓말로,
따뜻하지만 간결하게 말한다. 사용자를 "대표님", "고객님", "선생님" 같은 호칭으로
부르지 않는다 — 호칭 없이 문장을 만든다. 심문처럼 몰아붙이지 않는다 — 사용자의 말에 먼저 반응한
뒤 다음 항목으로 자연스럽게 넘어간다.

## 대화 단계 (순서대로, 한 턴에 질문 하나)

1. 대상 파악 — 어떤 종목/자산/테마인가
2. 관심 이유 — 왜 지금 눈에 들어왔나
3. 핵심 가설 — 무엇이 어떻게 되리라 보는가
4. 반대 시나리오 — "반대로 흘러간다면 어떤 모습일까요?"
5. 판단 시점 — "이건 언제쯤 판가름 날까요?" (아래 참고: 가장 집요하게)
6. 확인 방법 — "그날 무엇이 확인되면 답이 나오는 건가요?" (갈래의 질문 확보)

전체 5~8턴을 목표로 한다. 대부분의 항목은 한 번 묻고 넘어가되, 다음 둘만 끈질기게:

- **판단 시점**: 없으면 리마인드가 불가능하다. 막연하면 범위("올해 안", "2026 Q4까지")
  로라도 받아낸다.
- **근거 항목**: 없으면 회고 때 검증할 대상이 없어진다. 질문은
  "그렇게 되려면 그 전에 무슨 일이 먼저 일어나야 할까요?" 형태로 묻는다.
  수치나 데이터 출처는 묻지 않는다 — 사용자가 말로 설명한 그대로 받고,
  "구체적인 자료는 나중에 제가 찾아 정리하겠다"고 명시한다.

확인 방법의 목표값·비교 방식은 묻지 않는다 (2단계 폼의 일이다). 대화 중 나온 숫자는
기억만 해 둔다. 반증 질문("이 판단이 틀렸다는 건 뭘로 알 수 있을까요?")은 여유가
있을 때만 한다.

필수 항목(대상·가설·판단 시점·근거 항목)이 충분히 확보되었다고 판단하면, 이제
노트로 정리할 수 있겠다고 짧게 제안한다. 사용자가 원치 않으면 계속 대화한다.
"""
)

BUILD_SYSTEM = (
    _CONSTITUTION
    + """
## 역할

지금까지의 대화에서 노트의 칸을 채운다. 구조는 스키마가 강제하고 문장은 당신이 쓰되,
다음 규칙을 지킨다.

- `thesis_summary`: 한 문장, 40자 내외. `thesis_detail`: 사용자의 논리 재구성 3~5문장.
- `quote.text`: 대화에서 가장 핵심적인 사용자의 한 마디를 **원문 그대로** 옮긴다.
  한 글자도 다듬지 않는다.
- `premises[].statement`: 사용자가 말한 논리의 고리를 **말 그대로** 옮긴다. 다듬지
  않는다 — 나중에 검증할 대상이므로 원형이 중요하다. `quoted_text`에는 그 말이
  나온 사용자 발화 원문 구절을 넣는다.
- 갈래 나누기: 판가름 나는 날짜가 둘 이상이면 갈래를 나눈다. 같은 날이라도 질문이
  다르면 나눈다. 단, 사용자가 말하지 않은 질문을 지어내 갈래로 세우지 않는다.
- `scenarios[].name`: 질문에 대한 답만 담는다. "왜"는 description 으로 내린다.
- 날짜 해석: "올해 안" 같은 표현을 날짜로 해석했으면 `derived: true`로 표시하고
  `source_text`에 원 표현을 남긴다. 사용자가 날짜를 그대로 말했으면 `derived: false`.
- 시나리오가 1개뿐이거나 시점을 못 뽑았으면 지어내지 말고 비운 채 반환하고,
  `incomplete`에 무엇이 비었는지 적는다 (예: "no_deadline", "single_scenario").
- 확률은 어떤 칸에도 적지 않는다.
"""
)

_GREETING_DEFAULT = (
    "어떤 투자 아이디어를 정리해 볼까요? 종목이든 자산이든 테마든, "
    "지금 눈에 들어온 것부터 편하게 말씀해 주세요."
)


def greeting(seed_symbol: str | None = None) -> str:
    """대화 시작 시 첫 AI 인사 — 결정론적 템플릿(LLM 호출 없음, 비용·지연 0)."""
    if seed_symbol:
        return (
            f"{seed_symbol}에 대한 생각을 노트로 정리해 볼까요? "
            "어떤 점이 눈에 들어오셨는지부터 편하게 말씀해 주세요."
        )
    return _GREETING_DEFAULT


# ── 대화 이력 → API 메시지 ──────────────────────────────────────────────────


class Turn(NamedTuple):
    role: str  # user | assistant
    content: str


def _api_messages(turns: Sequence[Turn], today: date) -> list[MessageParam]:
    """DB 대화 이력을 API 메시지로. 오늘 날짜는 캐시 보호를 위해 시스템 프롬프트가
    아니라 첫 사용자 메시지로 주입한다. 첫 메시지는 user 여야 하므로 컨텍스트
    메시지가 그 역할을 겸한다."""
    context = f"(컨텍스트) 오늘 날짜: {today.isoformat()}. 대화를 시작합니다."
    messages: list[MessageParam] = [{"role": "user", "content": context}]
    for turn in turns:
        role: Any = turn.role  # DB CHECK 로 user|assistant 만 존재한다
        messages.append({"role": role, "content": turn.content})
    return messages


def _system_blocks(text: str) -> list[TextBlockParam]:
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


# ── (a) 대화 턴 — 스트리밍 ──────────────────────────────────────────────────


async def stream_reply(turns: Sequence[Turn], today: date) -> AsyncIterator[str]:
    """사용자 발화까지 포함된 이력을 받아 다음 assistant 턴을 토큰 단위로 낸다."""
    client = for_task(_CONVERSATION_TIMEOUT)
    async with client.messages.stream(
        model=MODEL,
        max_tokens=_CONVERSATION_MAX_TOKENS,
        system=_system_blocks(CONVERSATION_SYSTEM),
        messages=_api_messages(turns, today),
    ) as stream:
        async for text in stream.text_stream:
            yield text
        final = await stream.get_final_message()
        log_usage(
            agent=AGENT,
            model=MODEL,
            prompt_version=PROMPT_VERSION,
            usage=final.usage,
            request_id=getattr(final, "_request_id", None),
        )


# ── (b) 노트 조립 — tool 강제 구조화 출력 ───────────────────────────────────


class BuildTarget(BaseModel):
    type: str | None = None  # ticker | asset | theme
    symbol: str | None = None
    name: str = ""


class BuildJudge(BaseModel):
    kind: str | None = None  # date | range
    start: date | None = None
    end: date | None = None
    derived: bool = False
    source_text: str | None = None


class BuildScenario(BaseModel):
    name: str
    description: str | None = None


class BuildGalae(BaseModel):
    question: str = ""
    judge: BuildJudge = Field(default_factory=BuildJudge)
    scenarios: list[BuildScenario] = Field(default_factory=list)


class BuildPremise(BaseModel):
    statement: str
    quoted_text: str | None = None  # 이 논리가 나온 사용자 발화 원문 구절


class BuildQuote(BaseModel):
    text: str


class BuildNote(BaseModel):
    """조립 tool 의 출력 — NoteDraft 호환 + 인용 원문(quoted_text)·해석 표시(derived)."""

    target: BuildTarget = Field(default_factory=BuildTarget)
    thesis_summary: str = ""
    thesis_detail: str | None = None
    quote: BuildQuote | None = None
    galae: list[BuildGalae] = Field(default_factory=list)
    premises: list[BuildPremise] = Field(default_factory=list)
    incomplete: list[str] = Field(default_factory=list)


def _nullable_string() -> dict[str, Any]:
    return {"type": ["string", "null"]}


_BUILD_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "target",
        "thesis_summary",
        "thesis_detail",
        "quote",
        "galae",
        "premises",
        "incomplete",
    ],
    "properties": {
        "target": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "symbol", "name"],
            "properties": {
                "type": {
                    "anyOf": [
                        {"type": "string", "enum": ["ticker", "asset", "theme"]},
                        {"type": "null"},
                    ]
                },
                "symbol": _nullable_string(),
                "name": {"type": "string"},
            },
        },
        "thesis_summary": {"type": "string"},
        "thesis_detail": _nullable_string(),
        "quote": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["text"],
                    "properties": {"text": {"type": "string"}},
                },
                {"type": "null"},
            ]
        },
        "galae": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["question", "judge", "scenarios"],
                "properties": {
                    "question": {"type": "string"},
                    "judge": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["kind", "start", "end", "derived", "source_text"],
                        "properties": {
                            "kind": {
                                "anyOf": [
                                    {"type": "string", "enum": ["date", "range"]},
                                    {"type": "null"},
                                ]
                            },
                            "start": {
                                "anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]
                            },
                            "end": {
                                "anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]
                            },
                            "derived": {"type": "boolean"},
                            "source_text": _nullable_string(),
                        },
                    },
                    "scenarios": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "description"],
                            "properties": {
                                "name": {"type": "string"},
                                "description": _nullable_string(),
                            },
                        },
                    },
                },
            },
        },
        "premises": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement", "quoted_text"],
                "properties": {
                    "statement": {"type": "string"},
                    "quoted_text": _nullable_string(),
                },
            },
        },
        "incomplete": {"type": "array", "items": {"type": "string"}},
    },
}

_BUILD_TOOL: ToolParam = {
    "name": "build_note",
    "description": (
        "대화에서 확보한 내용으로 투자 노트의 칸을 채운다. 대화에 없는 내용은 채우지 "
        "않고 비운다. 인용(quote.text, premises[].quoted_text)은 사용자 발화 원문 그대로."
    ),
    "input_schema": _BUILD_TOOL_SCHEMA,
    "strict": True,
}

_BUILD_TOOL_CHOICE: ToolChoiceToolParam = {"type": "tool", "name": "build_note"}


class BuildFailedError(Exception):
    """스키마 검증 재시도까지 실패 — 호출부는 대화 화면으로 되돌린다."""


async def build_note(turns: Sequence[Turn], today: date) -> BuildNote:
    """대화 전문으로 노트를 조립한다. 스키마 검증 실패 시 오류를 붙여 1회 재시도."""
    client = for_task(_BUILD_TIMEOUT)
    messages = _api_messages(turns, today)
    messages.append(
        {
            "role": "user",
            "content": "여기까지의 대화로 노트를 조립해 주세요. build_note tool 로 반환하세요.",
        }
    )

    last_error = ""
    for attempt in range(2):
        if attempt > 0:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"방금 반환한 값이 스키마 검증에 실패했습니다: {last_error}\n"
                        "오류를 고쳐 build_note tool 로 다시 반환하세요."
                    ),
                }
            )
        response = await client.messages.create(
            model=MODEL,
            max_tokens=_BUILD_MAX_TOKENS,
            system=_system_blocks(BUILD_SYSTEM),
            messages=messages,
            tools=[_BUILD_TOOL],
            tool_choice=_BUILD_TOOL_CHOICE,
            thinking={"type": "disabled"},
        )
        log_usage(
            agent=f"{AGENT}.build",
            model=MODEL,
            prompt_version=PROMPT_VERSION,
            usage=response.usage,
            request_id=getattr(response, "_request_id", None),
        )
        tool_use = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_use is None:
            last_error = "tool_use 블록이 없습니다"
            continue
        try:
            return BuildNote.model_validate(tool_use.input)
        except ValidationError as e:
            last_error = str(e)
            messages.append({"role": "assistant", "content": response.content})
    raise BuildFailedError(last_error)


# ── 출처 대조 + NoteDraft 변환 (LLM 아님 — 검증 2층) ────────────────────────


@dataclass(frozen=True)
class AssembledDraft:
    draft: NoteDraft
    payload: dict[str, Any]  # conversations.draft_note 에 저장되는 미리보기 데이터


def assemble(build: BuildNote, user_messages: Sequence[tuple[UUID, str]]) -> AssembledDraft:
    """조립 출력을 출처 대조 후 NoteDraft + 미리보기 payload 로 변환한다.

    - premises: quoted_text(없으면 statement)를 user 발화에서 찾는다. 못 찾아도
      statement 는 유지하되 quoted_from 만 비운다 (인용 강등).
    - quote: 못 찾으면 authorship 을 ai 로 강등한다.
    - judge.derived=true 인 날짜는 derived_judges 로 모아 미리보기 확인 대상임을 명시한다.
    """
    premises: list[PremiseDraft] = []
    for p in build.premises:
        matched = find_quoted_from(p.quoted_text, user_messages) or find_quoted_from(
            p.statement, user_messages
        )
        premises.append(
            PremiseDraft(statement=p.statement, quoted_from=str(matched) if matched else None)
        )

    galae: list[GalaeDraft] = []
    derived_judges: list[dict[str, Any]] = []
    for i, g in enumerate(build.galae):
        kind: Any = g.judge.kind if g.judge.kind in ("date", "range") else None
        galae.append(
            GalaeDraft(
                question=g.question,
                judge_kind=kind,
                judge_start=g.judge.start,
                judge_end=g.judge.end,
                scenarios=[
                    ScenarioDraft(name=s.name, description=s.description) for s in g.scenarios
                ],
            )
        )
        if g.judge.derived and g.judge.end is not None:
            derived_judges.append(
                {
                    "galae_index": i,
                    "source_text": g.judge.source_text,
                    "judge_start": g.judge.start.isoformat() if g.judge.start else None,
                    "judge_end": g.judge.end.isoformat(),
                    "message": (
                        f'"{g.judge.source_text or "표현"}"을(를) '
                        f"{g.judge.end.isoformat()}로 읽었습니다. 미리보기에서 확인해 주세요."
                    ),
                }
            )

    known_types = ("ticker", "asset", "theme")
    target_type: Any = build.target.type if build.target.type in known_types else None
    draft = NoteDraft(
        target_type=target_type,
        target_symbol=build.target.symbol,
        target_name=build.target.name,
        thesis_summary=build.thesis_summary,
        thesis_detail=build.thesis_detail,
        galae=galae,
        premises=premises,
    )

    quote_payload: dict[str, Any] | None = None
    if build.quote is not None and build.quote.text.strip():
        quoted_from = find_quoted_from(build.quote.text, user_messages)
        quote_payload = {
            "text": build.quote.text,
            "quoted_from": str(quoted_from) if quoted_from else None,
            # 대조 실패 시 인용 강등 — [사용자] 표기가 붙지 않는다
            "authorship": "user" if quoted_from else "ai",
        }

    payload: dict[str, Any] = {
        "note": draft.model_dump(mode="json"),
        "quote": quote_payload,
        "derived_judges": derived_judges,
        "incomplete": build.incomplete,
        "agent": AGENT,
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
    }
    return AssembledDraft(draft=draft, payload=payload)
