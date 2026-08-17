"""노트 라우터 — DB 없이 검증되는 연결부만 단위 테스트한다.

저장·확률 갱신의 트랜잭션 경로는 DB 통합 테스트의 몫(02-backend §11) —
여기서는 검증기 연결(422)·residual 자동 추가·auto 조건 검사를 본다.
"""

from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import CurrentUser, current_user
from app.db.session import get_session
from app.domain.validation import GalaeDraft, NoteDraft, ScenarioDraft
from app.main import app
from app.routers.notes import RESIDUAL_NAME, check_auto_conditions, ensure_residual, pick_color

USER = CurrentUser(id="11111111-1111-1111-1111-111111111111", email="dev@example.com")


@pytest.fixture()
def client() -> Iterator[TestClient]:
    async def _no_db() -> Iterator[None]:  # DB 를 건드리기 전에 끝나는 경로만 테스트한다
        yield None

    app.dependency_overrides[current_user] = lambda: USER
    app.dependency_overrides[get_session] = _no_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── 순수 헬퍼 ───────────────────────────────────────────────────────────────


def test_ensure_residual_appends_when_missing() -> None:
    result = ensure_residual([ScenarioDraft(name="진입한다")])
    assert [s.name for s in result] == ["진입한다", RESIDUAL_NAME]
    assert result[-1].is_residual and result[-1].resolution_type == "complement"


def test_ensure_residual_keeps_existing() -> None:
    scenarios = [
        ScenarioDraft(name="진입한다"),
        ScenarioDraft(name=RESIDUAL_NAME, is_residual=True, resolution_type="complement"),
    ]
    assert ensure_residual(scenarios) == scenarios


def test_ensure_residual_on_empty_galae() -> None:
    # 시나리오가 하나도 없어도 residual 은 생긴다 — 갈래 생성 코드의 책임 (01-db-schema §4.3)
    (only,) = ensure_residual([])
    assert only.is_residual


def test_pick_color_is_deterministic_and_respects_explicit() -> None:
    a = NoteDraft(target_name="삼성전자")
    assert pick_color(a) == pick_color(NoteDraft(target_name="삼성전자"))
    assert pick_color(NoteDraft(target_name="삼성전자", color="#000000")) == "#000000"


def test_check_auto_conditions_rejects_incomplete() -> None:
    draft = NoteDraft(
        galae=[GalaeDraft(scenarios=[ScenarioDraft(name="넘는다", resolution_type="auto")])]
    )
    with pytest.raises(HTTPException) as e:
        check_auto_conditions(draft)
    assert e.value.status_code == 422
    detail = e.value.detail
    assert isinstance(detail, dict) and detail["code"] == "AUTO_CONDITION_INCOMPLETE"


def test_check_auto_conditions_passes_complete() -> None:
    draft = NoteDraft(
        galae=[
            GalaeDraft(
                scenarios=[
                    ScenarioDraft(
                        name="95,000원을 넘는다",
                        resolution_type="auto",
                        series_provider="kis",
                        series_code="005930",
                        comparator="gte",
                        target_value=95000,
                    ),
                    ScenarioDraft(name="직접 표시", resolution_type="manual"),
                ]
            )
        ]
    )
    check_auto_conditions(draft)  # 예외 없음


# ── 엔드포인트 — DB 를 건드리기 전에 끝나는 경로 ────────────────────────────


def test_validate_returns_issues_without_saving(client: TestClient) -> None:
    res = client.post("/notes/validate", json={})
    assert res.status_code == 200
    codes = {i["code"] for i in res.json()}
    assert codes == {"NO_TARGET", "NO_THESIS", "NO_DEADLINE", "NO_PREMISE"}


def test_validate_requires_auth() -> None:
    with TestClient(app) as anonymous:
        assert anonymous.post("/notes/validate", json={}).status_code == 401


def test_create_note_blocked_with_issues(client: TestClient) -> None:
    res = client.post("/notes", json={"thesis_summary": "가설만 있다"})
    assert res.status_code == 422
    (issue,) = res.json()["detail"]
    assert issue["code"] == "NO_TARGET"
    assert issue["severity"] == "blocking"


def test_create_note_rejects_incomplete_auto(client: TestClient) -> None:
    res = client.post(
        "/notes",
        json={
            "target_type": "ticker",
            "target_name": "삼성전자",
            "thesis_summary": "HBM4 진입이 리레이팅을 만든다",
            "galae": [{"scenarios": [{"name": "넘는다", "resolution_type": "auto"}]}],
        },
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "AUTO_CONDITION_INCOMPLETE"
