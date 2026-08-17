"""대화형 노트 작성(M3) — Claude 호출은 mock, DB 는 sqlite(aiosqlite) 파일로 대체한다.

검증 대상: 대화 턴 저장·seq 증가, build 의 출처 대조(있는 인용/변형된 인용/지어낸 인용),
저장 시 conversation attach 트랜잭션, 남의 대화 404.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agents import thesis_builder
from app.agents.thesis_builder import (
    BuildGalae,
    BuildJudge,
    BuildNote,
    BuildPremise,
    BuildQuote,
    BuildScenario,
    BuildTarget,
    Turn,
)
from app.auth import CurrentUser, current_user
from app.db.models import Base, ContentBlock, Conversation, ConversationMessage, Note
from app.db.session import get_session
from app.domain.quotes import find_quoted_from, normalize_ws
from app.main import app
from app.routers import conversations as conversations_router

USER = CurrentUser(id="11111111-1111-1111-1111-111111111111", email="dev@example.com")


@pytest.fixture()
def db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[async_sessionmaker[AsyncSession]]:
    # NullPool — 테스트가 여러 이벤트 루프(TestClient·asyncio.run)를 오가므로
    # 커넥션을 루프 간에 재사용하지 않는다.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db", poolclass=NullPool)
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
    # SSE 제너레이터는 의존성 밖에서 세션을 새로 연다 — 그 경로도 테스트 DB 로 돌린다
    monkeypatch.setattr(conversations_router, "get_sessionmaker", lambda: maker)
    yield maker
    app.dependency_overrides.clear()


@pytest.fixture()
def client(db: async_sessionmaker[AsyncSession]) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _fetch_all(
    maker: async_sessionmaker[AsyncSession], conversation_id: str
) -> list[ConversationMessage]:
    async def _run() -> list[ConversationMessage]:
        async with maker() as s:
            rows = await s.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == UUID(conversation_id))
                .order_by(ConversationMessage.seq)
            )
            return list(rows)

    return asyncio.run(_run())


def _mock_stream(monkeypatch: pytest.MonkeyPatch, chunks: Sequence[str]) -> None:
    async def fake_stream(turns: Sequence[Turn], today: date) -> AsyncIterator[str]:
        for c in chunks:
            yield c

    monkeypatch.setattr(thesis_builder, "stream_reply", fake_stream)


def _sse_events(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


# ── 순수 함수 — 출처 대조 ───────────────────────────────────────────────────


def test_normalize_ws_strips_all_whitespace() -> None:
    assert normalize_ws(" 삼성전자가\n HBM4 에 \t들어간다 ") == "삼성전자가HBM4에들어간다"


def test_find_quoted_from_matches_with_whitespace_variance() -> None:
    mid = uuid4()
    messages = [(mid, "삼성전자가 올해 안에\nHBM4 퀄 통과를 할 거라고 봐요")]
    assert find_quoted_from("올해 안에 HBM4 퀄 통과", messages) == mid
    assert find_quoted_from("엔비디아가 직접 만든다", messages) is None
    assert find_quoted_from(None, messages) is None
    assert find_quoted_from("   ", messages) is None


# ── 대화 시작·재개 ──────────────────────────────────────────────────────────


def test_create_conversation_saves_greeting(client: TestClient) -> None:
    res = client.post("/conversations", json={})
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "draft"
    assert body["note_id"] is None
    (msg,) = body["messages"]
    assert msg["role"] == "assistant" and msg["seq"] == 1
    assert msg["content"]  # 인사말이 저장돼 있다


def test_create_conversation_with_seed_symbol(client: TestClient) -> None:
    res = client.post("/conversations", json={"seed_symbol": "005930"})
    assert res.status_code == 201
    assert "005930" in res.json()["messages"][0]["content"]


def test_get_conversation_of_other_user_is_404(client: TestClient) -> None:
    conv_id = client.post("/conversations", json={}).json()["id"]
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="22222222-2222-2222-2222-222222222222", email="other@example.com"
    )
    assert client.get(f"/conversations/{conv_id}").status_code == 404
    turn = client.post(f"/conversations/{conv_id}/messages", json={"content": "hi"})
    assert turn.status_code == 404
    assert client.post(f"/conversations/{conv_id}/build").status_code == 404


def test_list_conversations_returns_drafts(client: TestClient) -> None:
    conv_id = client.post("/conversations", json={}).json()["id"]
    res = client.get("/conversations", params={"status": "draft"})
    assert res.status_code == 200
    assert conv_id in {c["id"] for c in res.json()}


# ── 대화 턴 — 저장·seq 증가·SSE ─────────────────────────────────────────────


def test_post_message_saves_turn_and_streams(
    client: TestClient, db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_stream(monkeypatch, ["반갑", "습니다. ", "어떤 점이 눈에 들어오셨나요?"])
    conv_id = client.post("/conversations", json={}).json()["id"]

    res = client.post(f"/conversations/{conv_id}/messages", json={"content": "삼성전자 이야기예요"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    events = _sse_events(res.text)
    assert events[0]["type"] == "user_message" and events[0]["seq"] == 2
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "반갑습니다. 어떤 점이 눈에 들어오셨나요?"
    done = events[-1]
    assert done["type"] == "done"
    assert done["message"]["seq"] == 3
    assert done["message"]["content"] == "반갑습니다. 어떤 점이 눈에 들어오셨나요?"

    saved = _fetch_all(db, conv_id)
    assert [(m.seq, m.role) for m in saved] == [(1, "assistant"), (2, "user"), (3, "assistant")]
    assert saved[1].content == "삼성전자 이야기예요"
    assert saved[2].content == "반갑습니다. 어떤 점이 눈에 들어오셨나요?"


def test_post_message_seq_keeps_increasing(
    client: TestClient, db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_stream(monkeypatch, ["네."])
    conv_id = client.post("/conversations", json={}).json()["id"]
    client.post(f"/conversations/{conv_id}/messages", json={"content": "첫 마디"})
    client.post(f"/conversations/{conv_id}/messages", json={"content": "둘째 마디"})
    saved = _fetch_all(db, conv_id)
    assert [m.seq for m in saved] == [1, 2, 3, 4, 5]
    assert [m.role for m in saved] == ["assistant", "user", "assistant", "user", "assistant"]


def test_post_message_keeps_user_turn_when_llm_fails(
    client: TestClient, db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(turns: Sequence[Turn], today: date) -> AsyncIterator[str]:
        raise RuntimeError("api down")
        yield ""  # pragma: no cover

    monkeypatch.setattr(thesis_builder, "stream_reply", broken)
    conv_id = client.post("/conversations", json={}).json()["id"]
    res = client.post(f"/conversations/{conv_id}/messages", json={"content": "저장은 되어야 한다"})
    events = _sse_events(res.text)
    assert events[-1]["type"] == "error"
    saved = _fetch_all(db, conv_id)
    # 사용자 발화는 스트리밍 전에 커밋되므로 유실되지 않는다 — assistant 는 저장 안 됨
    assert [(m.seq, m.role) for m in saved] == [(1, "assistant"), (2, "user")]


# ── 노트 조립 — 출처 대조 ───────────────────────────────────────────────────


def _build_output(user_text: str) -> BuildNote:
    return BuildNote(
        target=BuildTarget(type="ticker", symbol=None, name="삼성전자"),
        thesis_summary="HBM4 진입이 리레이팅을 만든다",
        thesis_detail="사용자는 HBM4 퀄 통과가 리레이팅의 방아쇠라고 본다.",
        quote=BuildQuote(text=user_text),  # 원문 그대로 → 대조 성공해야 한다
        galae=[
            BuildGalae(
                question="올해 안에 HBM4 퀄을 통과하는가?",
                judge=BuildJudge(
                    kind="date", end=date(2026, 12, 31), derived=True, source_text="올해 안"
                ),
                scenarios=[
                    BuildScenario(name="통과한다", description=None),
                    BuildScenario(name="통과하지 못한다", description=None),
                ],
            )
        ],
        premises=[
            # 1) 있는 인용 — 원문 그대로
            BuildPremise(statement=user_text, quoted_text=user_text),
            # 2) 변형된 인용 — 공백·개행이 달라도 정규화 후 매칭돼야 한다
            BuildPremise(
                statement="퀄   통과를\n해야", quoted_text="퀄   통과를\n해야"
            ),
            # 3) 지어낸 인용 — 대화에 없는 문장, 강등돼야 한다
            BuildPremise(
                statement="엔비디아가 직접 인증했다", quoted_text="엔비디아가 직접 인증했다"
            ),
        ],
        incomplete=[],
    )


def test_build_matches_quotes_and_saves_draft(
    client: TestClient, db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_stream(monkeypatch, ["네."])
    conv_id = client.post("/conversations", json={}).json()["id"]
    user_text = "삼성전자가 올해 안에 HBM4 퀄 통과를 해야 리레이팅이 온다고 봐요"
    client.post(f"/conversations/{conv_id}/messages", json={"content": user_text})
    user_msg_id = next(m for m in _fetch_all(db, conv_id) if m.role == "user").id

    async def fake_build(turns: Sequence[Turn], today: date) -> BuildNote:
        return _build_output(user_text)

    monkeypatch.setattr(thesis_builder, "build_note", fake_build)

    res = client.post(f"/conversations/{conv_id}/build")
    assert res.status_code == 200
    body = res.json()
    draft = body["draft_note"]

    # 대표 인용: 원문 그대로 → user 저작 + quoted_from
    assert draft["quote"]["authorship"] == "user"
    assert draft["quote"]["quoted_from"] == str(user_msg_id)

    p1, p2, p3 = draft["note"]["premises"]
    assert p1["quoted_from"] == str(user_msg_id)  # 있는 인용
    assert p2["quoted_from"] == str(user_msg_id)  # 변형된 인용 — 정규화 매칭
    assert p3["quoted_from"] is None  # 지어낸 인용 — 강등
    assert p3["statement"] == "엔비디아가 직접 인증했다"  # statement 는 유지된다

    # 날짜 해석은 derived 로 표시돼 미리보기 확인 대상임이 응답에 명시된다
    (dj,) = draft["derived_judges"]
    assert dj["source_text"] == "올해 안" and dj["judge_end"] == "2026-12-31"
    assert "확인" in dj["message"]

    # draft_note 가 conversations 에 저장된다 (재개용)
    reloaded = client.get(f"/conversations/{conv_id}").json()
    assert reloaded["draft_note"]["note"]["target_name"] == "삼성전자"

    # 검증기 Issue[] 가 함께 반환된다 (판단 시점·시나리오 2개가 있으므로 blocking 없음)
    assert all(i["severity"] != "blocking" for i in body["issues"])


def test_build_without_user_message_is_422(client: TestClient) -> None:
    conv_id = client.post("/conversations", json={}).json()["id"]
    res = client.post(f"/conversations/{conv_id}/build")
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "EMPTY_CONVERSATION"


# ── 저장 — conversation attach 트랜잭션 ─────────────────────────────────────


def _note_body(conv_id: str, quoted_from: str | None) -> dict[str, Any]:
    return {
        "conversation_id": conv_id,
        "target_type": "ticker",
        "target_name": "삼성전자",
        "thesis_summary": "HBM4 진입이 리레이팅을 만든다",
        "thesis_detail": "사용자의 논리 재구성.",
        "quote": {"text": "올해 안에 퀄 통과를 해야", "quoted_from": quoted_from},
        "galae": [
            {
                "question": "올해 안에 HBM4 퀄을 통과하는가?",
                "judge_kind": "date",
                "judge_end": "2026-12-31",
                "scenarios": [{"name": "통과한다"}, {"name": "통과하지 못한다"}],
            }
        ],
        "premises": [{"statement": "퀄 통과를 해야", "quoted_from": quoted_from}],
    }


def test_create_note_attaches_conversation(
    client: TestClient, db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_stream(monkeypatch, ["네."])
    conv_id = client.post("/conversations", json={}).json()["id"]
    client.post(f"/conversations/{conv_id}/messages", json={"content": "올해 안에 퀄 통과를 해야"})
    user_msg_id = str(next(m for m in _fetch_all(db, conv_id) if m.role == "user").id)

    res = client.post("/notes", json=_note_body(conv_id, user_msg_id))
    assert res.status_code == 201
    note_id = res.json()["id"]

    async def _check() -> tuple[Conversation, list[ContentBlock], Note]:
        async with db() as s:
            conv = await s.get(Conversation, UUID(conv_id))
            assert conv is not None
            blocks = list(
                (
                    await s.scalars(
                        select(ContentBlock)
                        .where(ContentBlock.note_id == UUID(note_id))
                        .order_by(ContentBlock.position)
                    )
                ).all()
            )
            note = await s.get(Note, UUID(note_id))
            assert note is not None
            return conv, blocks, note

    conv, blocks, _note = asyncio.run(_check())
    # 같은 트랜잭션에서 attach: note_id 연결 + status 전이
    assert conv.status == "attached"
    assert str(conv.note_id) == note_id
    # 가설·대표 인용이 content_blocks 로 저장된다
    thesis, quote = blocks
    assert thesis.section == "thesis" and thesis.authorship == "ai"
    assert quote.section == "thesis_quote" and quote.authorship == "user"
    assert str(quote.quoted_from) == user_msg_id


def test_create_note_demotes_bogus_quoted_from(
    client: TestClient, db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_stream(monkeypatch, ["네."])
    conv_id = client.post("/conversations", json={}).json()["id"]
    client.post(f"/conversations/{conv_id}/messages", json={"content": "아무 말"})

    res = client.post("/notes", json=_note_body(conv_id, str(uuid4())))  # 실재하지 않는 메시지
    assert res.status_code == 201
    note_id = res.json()["id"]

    async def _blocks() -> list[ContentBlock]:
        async with db() as s:
            rows = await s.scalars(
                select(ContentBlock).where(ContentBlock.note_id == UUID(note_id))
            )
            return list(rows)

    blocks = asyncio.run(_blocks())
    quote = next(b for b in blocks if b.section == "thesis_quote")
    assert quote.authorship == "ai" and quote.quoted_from is None  # 강등

    # premises 의 quoted_from 도 강등된다
    detail = client.get(f"/notes/{note_id}").json()
    assert detail["premises"][0]["quoted_from"] is None


def test_create_note_with_other_users_conversation_is_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv_id = client.post("/conversations", json={}).json()["id"]
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="22222222-2222-2222-2222-222222222222", email="other@example.com"
    )
    res = client.post("/notes", json=_note_body(conv_id, None))
    assert res.status_code == 404


def test_create_note_rejects_already_attached_conversation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_stream(monkeypatch, ["네."])
    conv_id = client.post("/conversations", json={}).json()["id"]
    client.post(f"/conversations/{conv_id}/messages", json={"content": "아무 말"})
    assert client.post("/notes", json=_note_body(conv_id, None)).status_code == 201
    res = client.post("/notes", json=_note_body(conv_id, None))
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "CONVERSATION_NOT_DRAFT"

    # attach 된 대화에는 더 이상 턴을 추가할 수 없다
    res = client.post(f"/conversations/{conv_id}/messages", json={"content": "더 말하기"})
    assert res.status_code == 409
