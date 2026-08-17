"""확률 재분배 — 합이 100이 아닌 상태를 만들 방법 자체를 없앤다.

"합이 100%가 아니다"는 검사 대상이 아니다. 개별 시나리오의 확률을 따로 고치는
경로를 열지 않고, 갈래 단위 원자적 갱신(PATCH /galae/{id}/probabilities) 안에서
이 순수 함수가 합 100인 배분만 만들어낸다.
근거: docs/development-plan.md §3.1 "확률 합은 검사하지 않는다", docs/dev/02-backend.md §6.

불변식 셋 — 합계 = 100 · 모든 값이 5의 배수 · 잔여 슬롯(is_residual) ≥ 5.
골든 벡터: 리포 루트 fixtures/probability.json (TS 슬라이더 미러와 같은 파일을 읽는다).
"""

from collections.abc import Sequence, Set
from dataclasses import dataclass

# 확률은 5% 단위로만 존재한다 (§3.1)
STEP = 5
# `그 외 예상 못한 전개`의 최소치 — 삭제 불가·최소 5% (01-db-schema §3.4)
RESIDUAL_MIN = 5


@dataclass(frozen=True)
class ScenarioProb:
    """재분배 입력 한 줄 — scenarios 행에서 확률 관련 칼럼만 뗀 것."""

    id: str
    probability: int | None = None  # None = 아직 배분되지 않음
    is_residual: bool = False


def redistribute(
    scenarios: Sequence[ScenarioProb],
    changed_id: str,
    new_value: int,
    locked_ids: Set[str] | None = None,
) -> dict[str, int] | None:
    """시나리오 하나를 움직였을 때 갈래 전체의 새 배분을 돌려준다.

    시나리오가 residual 포함 하나뿐이면 갈래가 성립하지 않으므로 None을 돌려
    확률을 전부 비운다 — 혼자인 답에 100%를 넣으면 사용자가 표현한 적 없는
    확신을 앱이 만들어내는 셈이다 (02-backend §6).
    """
    locked_set = locked_ids if locked_ids is not None else frozenset()

    ids = {s.id for s in scenarios}
    if changed_id not in ids:
        raise ValueError(f"갈래에 없는 시나리오다: {changed_id}")
    if changed_id in locked_set:
        raise ValueError("잠긴 시나리오는 움직일 수 없다")

    if len(scenarios) < 2:
        return None

    residuals = [s for s in scenarios if s.is_residual]
    if len(residuals) > 1:
        raise ValueError("잔여 슬롯은 갈래에 하나뿐이다")
    residual_id = residuals[0].id if residuals else None

    # 잠긴 값은 그대로 옮긴다. 배분 전(None)인데 잠겼다면 0으로 본다.
    locked: dict[str, int] = {
        s.id: s.probability or 0 for s in scenarios if s.id in locked_set
    }
    locked_sum = sum(locked.values())

    # 1. 새 값을 5단위로 스냅하고, 잠긴 값들과 잔여 슬롯 최소치(5)가 들어갈
    #    자리를 뺀 상한으로 자른다 — 100을 입력해도 95로 잘린다.
    #    (정수 입력이라 /5 의 소수부는 .5 를 지나지 않으므로 round 의 은행가
    #    반올림 문제는 생기지 않는다)
    snapped = STEP * round(new_value / STEP)
    reserve = (
        RESIDUAL_MIN
        if residual_id is not None and residual_id != changed_id and residual_id not in locked
        else 0
    )
    # 움직인 것이 잔여 슬롯 자신이면 최소치 5 아래로는 내려가지 않는다.
    lower = RESIDUAL_MIN if residual_id == changed_id else 0
    upper = max(0, 100 - locked_sum - reserve)
    value = min(max(snapped, lower), upper)

    others = [s for s in scenarios if s.id != changed_id and s.id not in locked]
    if not others:
        # 나머지가 전부 잠겼으면 바꾼 값은 여집합으로 강제된다 — 합 100이 스냅보다 우선한다.
        return {changed_id: 100 - locked_sum, **locked}

    remaining = 100 - value - locked_sum

    # 2. 남은 몫을 나머지 시나리오들의 기존 비율대로 나눈다.
    #    아직 아무도 배분이 없으면(전부 None 또는 0) 균등 비율로 본다.
    weights = [s.probability or 0 for s in others]
    if sum(weights) == 0:
        weights = [1] * len(others)
    total_w = sum(weights)

    # 3. 각각 5단위로 내린 뒤, 부족분을 최대 잔여법(largest remainder)으로 채워
    #    합계를 정확히 100으로 맞춘다. 부동소수점 오차를 피하려고 정수 산술로만
    #    계산한다 — 몫·나머지의 분모(total_w × STEP)가 같아 나머지끼리 비교 가능하다.
    denom = total_w * STEP
    floors = [STEP * (remaining * w // denom) for w in weights]
    rems = [remaining * w % denom for w in weights]
    deficit = remaining - sum(floors)
    for idx in sorted(range(len(others)), key=lambda k: (-rems[k], k)):
        if deficit == 0:
            break
        floors[idx] += STEP
        deficit -= STEP

    result: dict[str, int] = {changed_id: value, **locked}
    for scenario, allocated in zip(others, floors, strict=True):
        result[scenario.id] = allocated

    # 4. 잔여 슬롯이 5 미만이면 가장 큰 항목에서 빌려와 채운다.
    #    바꾼 값과 잠긴 값은 건드리지 않는다 — 1단계의 상한 절단이 나머지
    #    시나리오들에게 항상 충분한 몫을 보장하므로 여기서 부족할 수 없다.
    if (
        residual_id is not None
        and residual_id not in locked
        and result[residual_id] < RESIDUAL_MIN
    ):
        need = RESIDUAL_MIN - result[residual_id]
        donors = sorted(
            (s for s in others if s.id != residual_id),
            key=lambda s: -result[s.id],
        )
        for donor in donors:
            take = min(need, result[donor.id])
            result[donor.id] -= take
            need -= take
            if need == 0:
                break
        result[residual_id] = RESIDUAL_MIN - need

    # 불변식 — 함수가 1차 방어, DB deferred constraint 가 최후 방어다.
    assert sum(result.values()) == 100
    assert all(v % STEP == 0 for v in result.values())
    return result
