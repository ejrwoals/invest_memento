"""대화형 노트 작성(Thesis Builder) — 대화 시작·재개·턴 진행(SSE)·노트 조립.

- 모든 쿼리는 user_id 스코핑, 남의 대화는 404 (02-backend §3).
- conversation_messages 는 불변(DB 트리거) — 이 라우터는 INSERT 만 한다.
- 턴 진행은 유일한 동기 LLM 호출이라 SSE(text/event-stream)로 스트리밍한다.
  스트리밍 중 DB 세션: FastAPI 의존성 세션은 응답 전에 닫힐 수 있으므로,
  assistant 메시지 저장은 제너레이터 안에서 새 세션을 연다.
"""

import json
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import thesis_builder
from app.agents.thesis_builder import BuildFailedError, Turn
from app.auth import RequireUser
from app.db import SessionDep
from app.db.models import Conversation, ConversationMessage
from app.db.session import get_sessionmaker
from app.domain.validation import validate_note
from app.routers.notes import IssueOut

router = APIRouter()

# 대화당 사용자 턴 상한 (04-ai-agents §3.5 — 5~8턴 목표의 안전판)
TURN_LIMIT = 30


# ── 스키마 ──────────────────────────────────────────────────────────────────


class ConversationCreate(BaseModel):
    seed_symbol: str | None = None


class UserMessageIn(BaseModel):
    content: str


class MessageOut(BaseModel):
    id: UUID
    seq: int
    role: str
    content: str
    created_at: datetime


class ConversationOut(BaseModel):
    id: UUID
    status: str
    note_id: UUID | None
    draft_note: dict[str, Any] | None
    messages: list[MessageOut]


class ConversationSummary(BaseModel):
    id: UUID
    status: str
    updated_at: datetime
    preview: str  # 첫 사용자 발화(없으면 인사말) 앞부분 — 재개 목록 표시용


class BuildOut(BaseModel):
    draft_note: dict[str, Any]
    issues: list[IssueOut]


# ── 헬퍼 ────────────────────────────────────────────────────────────────────


def _message_out(m: ConversationMessage) -> MessageOut:
    return MessageOut(id=m.id, seq=m.seq, role=m.role, content=m.content, created_at=m.created_at)


async def _load_conversation(
    session: AsyncSession, user_id: UUID, conversation_id: UUID
) -> Conversation:
    conv = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )
    if conv is None:
        # 남의 대화든 없는 대화든 404 — 존재 여부를 흘리지 않는다
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


def _require_draft(conv: Conversation) -> None:
    if conv.status != "draft":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONVERSATION_NOT_DRAFT",
                "message": "이미 노트로 저장되었거나 종료된 대화입니다.",
            },
        )


async def _messages_of(session: AsyncSession, conversation_id: UUID) -> list[ConversationMessage]:
    rows = await session.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.seq)
    )
    return list(rows)


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── 엔드포인트 ──────────────────────────────────────────────────────────────


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: ConversationCreate, user: RequireUser, session: SessionDep
) -> ConversationOut:
    """대화 시작 — 첫 AI 인사 메시지를 생성해 함께 저장한다."""
    conv = Conversation(user_id=UUID(user.id))
    first = ConversationMessage(
        seq=1, role="assistant", content=thesis_builder.greeting(body.seed_symbol)
    )
    conv.messages.append(first)
    session.add(conv)
    await session.commit()
    return ConversationOut(
        id=conv.id,
        status=conv.status,
        note_id=conv.note_id,
        draft_note=conv.draft_note,
        messages=[_message_out(m) for m in conv.messages],
    )


@router.get("/conversations")
async def list_conversations(
    user: RequireUser, session: SessionDep, status: str = "draft"
) -> list[ConversationSummary]:
    """재개 목록 (홈 상단 링크용). 기본은 draft 만."""
    convs = (
        await session.scalars(
            select(Conversation)
            .where(Conversation.user_id == UUID(user.id), Conversation.status == status)
            .order_by(Conversation.updated_at.desc())
        )
    ).all()
    summaries: list[ConversationSummary] = []
    for conv in convs:
        preview = await session.scalar(
            select(ConversationMessage.content)
            .where(
                ConversationMessage.conversation_id == conv.id,
                ConversationMessage.role == "user",
            )
            .order_by(ConversationMessage.seq)
            .limit(1)
        )
        if preview is None:
            preview = await session.scalar(
                select(ConversationMessage.content)
                .where(ConversationMessage.conversation_id == conv.id)
                .order_by(ConversationMessage.seq)
                .limit(1)
            )
        summaries.append(
            ConversationSummary(
                id=conv.id,
                status=conv.status,
                updated_at=conv.updated_at,
                preview=(preview or "")[:80],
            )
        )
    return summaries


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID, user: RequireUser, session: SessionDep
) -> ConversationOut:
    """메시지 목록 + draft_note — 대화 재개용."""
    conv = await _load_conversation(session, UUID(user.id), conversation_id)
    messages = await _messages_of(session, conv.id)
    return ConversationOut(
        id=conv.id,
        status=conv.status,
        note_id=conv.note_id,
        draft_note=conv.draft_note,
        messages=[_message_out(m) for m in messages],
    )


@router.post("/conversations/{conversation_id}/messages")
async def post_message(
    conversation_id: UUID, body: UserMessageIn, user: RequireUser, session: SessionDep
) -> StreamingResponse:
    """사용자 발화 저장 → Claude 스트리밍 → SSE 토큰 스트림 → 완료 시 assistant 저장."""
    if not body.content.strip():
        raise HTTPException(status_code=422, detail="content is empty")
    conv = await _load_conversation(session, UUID(user.id), conversation_id)
    _require_draft(conv)

    user_turns = await session.scalar(
        select(func.count())
        .select_from(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conv.id,
            ConversationMessage.role == "user",
        )
    )
    if (user_turns or 0) >= TURN_LIMIT:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "TURN_LIMIT",
                "message": "대화가 너무 길어졌습니다. 여기까지 정리하기로 노트를 만들어 주세요.",
            },
        )

    # 사용자 발화는 스트리밍 전에 커밋한다 — 대화 이력은 서버가 정본이므로
    # 이후 스트리밍이 실패해도 유실되지 않는다.
    next_seq = (
        await session.scalar(
            select(func.coalesce(func.max(ConversationMessage.seq), 0)).where(
                ConversationMessage.conversation_id == conv.id
            )
        )
        or 0
    ) + 1
    user_message = ConversationMessage(
        conversation_id=conv.id, seq=next_seq, role="user", content=body.content
    )
    session.add(user_message)
    await session.commit()

    history = [Turn(role=m.role, content=m.content) for m in await _messages_of(session, conv.id)]
    conv_id = conv.id
    assistant_seq = next_seq + 1

    async def event_stream() -> AsyncIterator[str]:
        yield _sse({"type": "user_message", "id": str(user_message.id), "seq": user_message.seq})
        chunks: list[str] = []
        try:
            async for chunk in thesis_builder.stream_reply(history, date.today()):
                chunks.append(chunk)
                yield _sse({"type": "delta", "text": chunk})
        except Exception:  # LLM 실패 — 사용자 발화는 이미 저장돼 있어 재시도 가능하다
            failure = "응답 생성에 실패했습니다. 잠시 후 다시 시도해 주세요."
            yield _sse({"type": "error", "message": failure})
            return
        content = "".join(chunks)
        # 의존성 세션은 이 시점에 닫혀 있을 수 있다 — 저장용 새 세션을 연다
        async with get_sessionmaker()() as save_session:
            assistant = ConversationMessage(
                conversation_id=conv_id, seq=assistant_seq, role="assistant", content=content
            )
            save_session.add(assistant)
            await save_session.commit()
            yield _sse(
                {
                    "type": "done",
                    "message": {
                        "id": str(assistant.id),
                        "seq": assistant.seq,
                        "role": "assistant",
                        "content": content,
                    },
                }
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/conversations/{conversation_id}/build")
async def build(conversation_id: UUID, user: RequireUser, session: SessionDep) -> BuildOut:
    """조립 실행 → 출처 대조 → NoteDraft + Issue[] 반환, draft_note 에 저장(미리보기)."""
    conv = await _load_conversation(session, UUID(user.id), conversation_id)
    _require_draft(conv)
    messages = await _messages_of(session, conv.id)
    if not any(m.role == "user" for m in messages):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EMPTY_CONVERSATION",
                "message": "아직 대화 내용이 없어 노트를 조립할 수 없습니다.",
            },
        )

    turns = [Turn(role=m.role, content=m.content) for m in messages]
    try:
        built = await thesis_builder.build_note(turns, date.today())
    except BuildFailedError as e:
        # 내부 사정(스키마 검증 실패)은 화면에 내보내지 않는다 (UX §9.3)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "BUILD_FAILED",
                "message": "노트 정리에 실패했습니다. 대화로 돌아가 다시 시도해 주세요.",
            },
        ) from e

    user_messages = [(m.id, m.content) for m in messages if m.role == "user"]
    assembled = thesis_builder.assemble(built, user_messages)
    issues = validate_note(assembled.draft)

    conv.draft_note = assembled.payload
    await session.commit()

    return BuildOut(
        draft_note=assembled.payload, issues=[IssueOut.from_issue(i) for i in issues]
    )
