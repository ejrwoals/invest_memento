"""노트 검증기 — 규칙별 케이스. 규칙 표: development-plan.md §3.1."""

from datetime import date, timedelta

from app.domain.validation import (
    GalaeDraft,
    Issue,
    NoteDraft,
    PremiseDraft,
    ScenarioDraft,
    check_quoted_sources,
    validate_note,
)

TODAY = date(2026, 8, 17)


def _complete_draft() -> NoteDraft:
    return NoteDraft(
        target_type="ticker",
        target_symbol="005930",
        target_name="삼성전자",
        thesis_summary="HBM4 진입이 리레이팅을 만든다",
        galae=[
            GalaeDraft(
                question="올해 안에 HBM4 공급사로 진입하는가?",
                judge_kind="date",
                judge_end=date(2026, 12, 31),
                scenarios=[
                    ScenarioDraft(
                        name="12월 말까지 95,000원을 넘는다",
                        resolution_type="auto",
                    ),
                    ScenarioDraft(
                        name="그 외 예상 못한 전개",
                        resolution_type="complement",
                        is_residual=True,
                    ),
                ],
            )
        ],
        premises=[PremiseDraft(statement="HBM 공급이 계속 부족해야 하고")],
    )


def _codes(issues: list[Issue]) -> set[str]:
    return {i.code for i in issues}


def test_complete_note_has_no_issues() -> None:
    assert validate_note(_complete_draft(), TODAY) == []


def test_empty_draft() -> None:
    issues = validate_note(NoteDraft(), TODAY)
    assert _codes(issues) == {"NO_TARGET", "NO_THESIS", "NO_DEADLINE", "NO_PREMISE"}


def test_no_target_is_blocking() -> None:
    draft = _complete_draft().model_copy(update={"target_name": "  "})
    (issue,) = [i for i in validate_note(draft, TODAY) if i.code == "NO_TARGET"]
    assert issue.severity == "blocking"
    assert issue.field == "target_name"


def test_no_thesis_is_blocking() -> None:
    draft = _complete_draft().model_copy(update={"thesis_summary": ""})
    (issue,) = [i for i in validate_note(draft, TODAY) if i.code == "NO_THESIS"]
    assert issue.severity == "blocking"


def test_no_deadline_when_no_galae_has_judge_end() -> None:
    draft = _complete_draft()
    draft.galae[0].judge_end = None
    draft.galae[0].judge_kind = None
    (issue,) = [i for i in validate_note(draft, TODAY) if i.code == "NO_DEADLINE"]
    assert issue.severity == "ask"
    assert issue.fix is not None


def test_no_deadline_passes_if_any_galae_is_dated() -> None:
    # 판단 시점 있는 갈래가 하나라도 있으면 리마인드가 돌므로 노트 단위로는 통과
    draft = _complete_draft()
    draft.galae.append(GalaeDraft(question="주가 갈래", scenarios=[]))
    assert "NO_DEADLINE" not in _codes(validate_note(draft, TODAY))


def test_single_scenario_asks_once() -> None:
    draft = _complete_draft()
    draft.galae[0].scenarios = draft.galae[0].scenarios[:1]
    (issue,) = [i for i in validate_note(draft, TODAY) if i.code == "SINGLE_SCENARIO"]
    assert issue.severity == "ask"
    assert issue.field == "galae[0].scenarios"


def test_no_premise() -> None:
    draft = _complete_draft().model_copy(update={"premises": []})
    (issue,) = [i for i in validate_note(draft, TODAY) if i.code == "NO_PREMISE"]
    assert issue.severity == "ask"


def test_no_galae_question_only_when_scenarios_exist() -> None:
    draft = _complete_draft()
    draft.galae[0].question = ""
    (issue,) = [i for i in validate_note(draft, TODAY) if i.code == "NO_GALAE_QUESTION"]
    assert issue.severity == "ask"

    # 시나리오가 없으면 이 규칙의 대상이 아니다
    draft.galae[0].scenarios = []
    assert "NO_GALAE_QUESTION" not in _codes(validate_note(draft, TODAY))


def test_no_auto_resolution_is_notice() -> None:
    draft = _complete_draft()
    draft.galae[0].scenarios[0].resolution_type = "manual"
    (issue,) = [i for i in validate_note(draft, TODAY) if i.code == "NO_AUTO_RESOLUTION"]
    assert issue.severity == "notice"


def test_deadline_in_past_is_notice() -> None:
    draft = _complete_draft()
    draft.galae[0].judge_end = TODAY - timedelta(days=1)
    (issue,) = [i for i in validate_note(draft, TODAY) if i.code == "DEADLINE_IN_PAST"]
    assert issue.severity == "notice"


def test_deadline_today_is_not_past() -> None:
    draft = _complete_draft()
    draft.galae[0].judge_end = TODAY
    assert "DEADLINE_IN_PAST" not in _codes(validate_note(draft, TODAY))


def test_messages_are_complete_sentences() -> None:
    # message 는 UI가 그대로 쓸 완성된 한국어 문장이어야 한다 (§3.1)
    issues = validate_note(NoteDraft(), TODAY)
    assert issues and all(i.message.endswith("니다.") for i in issues)


def test_quote_check_is_not_in_scope_yet() -> None:
    # 2층 출처 대조는 M2 범위 밖 — 자리만 있고 아무것도 잡지 않는다
    assert check_quoted_sources(_complete_draft(), ["HBM 공급이 계속 부족해야 하고"]) == []
