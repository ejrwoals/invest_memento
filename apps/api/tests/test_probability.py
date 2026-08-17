"""확률 재분배 — 골든 벡터 전 케이스 + 불변식 property 테스트.

골든 벡터(fixtures/probability.json)는 TS 슬라이더 미러(vitest)와 같은 파일이다.
벡터 수정은 정본 수정과 같다 — fixtures/README.md.
"""

import json
import random
from pathlib import Path
from typing import Any

import pytest

from app.domain.probability import RESIDUAL_MIN, STEP, ScenarioProb, redistribute

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "probability.json"

with FIXTURE.open(encoding="utf-8") as f:
    CASES: list[dict[str, Any]] = json.load(f)


def _scenarios(case: dict[str, Any]) -> list[ScenarioProb]:
    return [
        ScenarioProb(id=s["id"], probability=s["probability"], is_residual=s["is_residual"])
        for s in case["scenarios"]
    ]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_golden_vectors(case: dict[str, Any]) -> None:
    result = redistribute(
        _scenarios(case),
        case["changed"]["id"],
        case["changed"]["value"],
        frozenset(case["locked_ids"]),
    )
    assert result == case["expected"]


def test_unknown_scenario_raises() -> None:
    with pytest.raises(ValueError):
        redistribute([ScenarioProb("A", 50), ScenarioProb("R", 50, True)], "X", 30)


def test_locked_changed_raises() -> None:
    with pytest.raises(ValueError):
        redistribute([ScenarioProb("A", 50), ScenarioProb("R", 50, True)], "A", 30, {"A"})


def test_invariants_hold_for_random_inputs() -> None:
    """임의 입력에 대해 합계 = 100 · 5의 배수 · residual >= 5 · 잠긴 값 불변."""
    rng = random.Random(20260817)
    for _ in range(500):
        n = rng.randint(2, 6)
        ids = [f"s{i}" for i in range(n)]
        residual_id = ids[-1]

        first_allocation = rng.random() < 0.2
        if first_allocation:
            probs: dict[str, int | None] = dict.fromkeys(ids)
        else:
            # 유효한 시작 상태: 5단위 합 100, residual >= 5
            units = [0] * n
            units[-1] = 1
            for _ in range(19):
                units[rng.randrange(n)] += 1
            probs = {ids[i]: units[i] * STEP for i in range(n)}

        scenarios = [
            ScenarioProb(id=i, probability=probs[i], is_residual=(i == residual_id)) for i in ids
        ]
        changed_id = rng.choice(ids)
        # 배분 전 상태에서는 잠글 값이 없으므로 잠금을 걸지 않는다
        lockable = [] if first_allocation else [i for i in ids if i != changed_id]
        locked = frozenset(rng.sample(lockable, rng.randint(0, len(lockable))))
        new_value = rng.randint(0, 100)

        result = redistribute(scenarios, changed_id, new_value, locked)

        assert result is not None
        assert result.keys() == set(ids)
        assert sum(result.values()) == 100
        assert all(v % STEP == 0 for v in result.values())
        assert result[residual_id] >= RESIDUAL_MIN
        for lid in locked:
            assert result[lid] == probs[lid]


def test_single_scenario_returns_none() -> None:
    assert redistribute([ScenarioProb("only", None, True)], "only", 50) is None
