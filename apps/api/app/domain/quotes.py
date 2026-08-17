"""출처 대조 — 검증 2층. LLM 이 아니라 결정론적 코드가 한다 (04-ai-agents §5).

"사용자가 한 말"이라고 주장하는 모든 출력(quote·premise 의 quoted_text)은 저장 전에
원본 대화의 user 메시지에서 실제 문자열을 찾는다. 대조는 공백·개행 정규화 후
부분 문자열 일치. 못 찾으면 인용 표시를 떼고 AI 저작으로 강등한다 — 차단이 아니라
강등이다: 내용은 남되 [사용자]가 안 붙는다.
"""

from collections.abc import Sequence
from uuid import UUID


def normalize_ws(text: str) -> str:
    """공백·개행을 전부 제거해 비교 가능한 형태로 만든다."""
    return "".join(text.split())


def find_quoted_from(
    quoted_text: str | None, user_messages: Sequence[tuple[UUID, str]]
) -> UUID | None:
    """quoted_text 가 실제로 등장하는 user 메시지의 id 를 찾는다. 없으면 None(강등)."""
    if not quoted_text:
        return None
    needle = normalize_ws(quoted_text)
    if not needle:
        return None
    for message_id, content in user_messages:
        if needle in normalize_ws(content):
            return message_id
    return None
