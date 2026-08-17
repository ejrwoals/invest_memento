// 확률 재분배 — apps/api/app/domain/probability.py 의 TS 미러.
// 정본은 서버다 (docs/dev/02-backend §6). 이 미러는 슬라이더 드래그 중 매 프레임
// 미리보기 전용이고, 저장은 changed + locked_ids 만 보내 서버 재분배 결과로 덮는다.
// 어긋남은 골든 벡터(fixtures/probability.json)를 pytest·vitest 가 같이 읽어 잡는다.
//
// 불변식 셋 — 합계 = 100 · 모든 값이 5의 배수 · 잔여 슬롯(is_residual) ≥ 5.

/** 확률은 5% 단위로만 존재한다 (development-plan §3.1) */
export const STEP = 5;
/** `그 외 예상 못한 전개`의 최소치 — 삭제 불가·최소 5% */
export const RESIDUAL_MIN = 5;

/** 재분배 입력 한 줄 — 서버 ScenarioProb 와 같은 모양 */
export interface ScenarioProb {
  id: string;
  probability: number | null; // null = 아직 배분되지 않음
  is_residual: boolean;
}

const sum = (xs: readonly number[]): number => xs.reduce((a, b) => a + b, 0);

/**
 * 시나리오 하나를 움직였을 때 갈래 전체의 새 배분을 돌려준다.
 *
 * 시나리오가 residual 포함 하나뿐이면 갈래가 성립하지 않으므로 null —
 * 혼자인 답에 100%를 넣으면 사용자가 표현한 적 없는 확신을 앱이 만들어낸다.
 */
export function redistribute(
  scenarios: readonly ScenarioProb[],
  changedId: string,
  newValue: number,
  lockedIds: ReadonlySet<string> = new Set(),
): Record<string, number> | null {
  if (!scenarios.some((s) => s.id === changedId)) {
    throw new Error(`갈래에 없는 시나리오다: ${changedId}`);
  }
  if (lockedIds.has(changedId)) throw new Error("잠긴 시나리오는 움직일 수 없다");

  if (scenarios.length < 2) return null;

  const residuals = scenarios.filter((s) => s.is_residual);
  if (residuals.length > 1) throw new Error("잔여 슬롯은 갈래에 하나뿐이다");
  const residualId = residuals.length === 1 ? residuals[0].id : null;

  // 잠긴 값은 그대로 옮긴다. 배분 전(null)인데 잠겼다면 0으로 본다.
  const locked: Record<string, number> = {};
  for (const s of scenarios) if (lockedIds.has(s.id)) locked[s.id] = s.probability ?? 0;
  const lockedSum = sum(Object.values(locked));

  // 1. 새 값을 5단위로 스냅하고, 잠긴 값들과 잔여 슬롯 최소치(5)가 들어갈
  //    자리를 뺀 상한으로 자른다 — 100을 입력해도 95로 잘린다.
  const snapped = STEP * Math.round(newValue / STEP);
  const reserve =
    residualId !== null && residualId !== changedId && !(residualId in locked) ? RESIDUAL_MIN : 0;
  // 움직인 것이 잔여 슬롯 자신이면 최소치 5 아래로는 내려가지 않는다.
  const lower = residualId === changedId ? RESIDUAL_MIN : 0;
  const upper = Math.max(0, 100 - lockedSum - reserve);
  const value = Math.min(Math.max(snapped, lower), upper);

  const others = scenarios.filter((s) => s.id !== changedId && !(s.id in locked));
  if (others.length === 0) {
    // 나머지가 전부 잠겼으면 바꾼 값은 여집합으로 강제된다 — 합 100이 스냅보다 우선한다.
    return { [changedId]: 100 - lockedSum, ...locked };
  }

  const remaining = 100 - value - lockedSum;

  // 2. 남은 몫을 나머지 시나리오들의 기존 비율대로 나눈다.
  //    아직 아무도 배분이 없으면(전부 null 또는 0) 균등 비율로 본다.
  let weights = others.map((s) => s.probability ?? 0);
  if (sum(weights) === 0) weights = others.map(() => 1);
  const totalW = sum(weights);

  // 3. 각각 5단위로 내린 뒤, 부족분을 최대 잔여법(largest remainder)으로 채워
  //    합계를 정확히 100으로 맞춘다. 정수 산술만 쓴다 — 서버와 자리수까지 같다.
  const denom = totalW * STEP;
  const floors = weights.map((w) => STEP * Math.floor((remaining * w) / denom));
  const rems = weights.map((w) => (remaining * w) % denom);
  let deficit = remaining - sum(floors);
  const order = others.map((_, k) => k).sort((a, b) => rems[b] - rems[a] || a - b);
  for (const idx of order) {
    if (deficit === 0) break;
    floors[idx] += STEP;
    deficit -= STEP;
  }

  const result: Record<string, number> = { [changedId]: value, ...locked };
  others.forEach((s, i) => {
    result[s.id] = floors[i];
  });

  // 4. 잔여 슬롯이 5 미만이면 가장 큰 항목에서 빌려와 채운다.
  //    바꾼 값과 잠긴 값은 건드리지 않는다.
  if (residualId !== null && !(residualId in locked) && result[residualId] < RESIDUAL_MIN) {
    let need = RESIDUAL_MIN - result[residualId];
    const donors = others
      .filter((s) => s.id !== residualId)
      .sort((a, b) => result[b.id] - result[a.id]);
    for (const donor of donors) {
      const take = Math.min(need, result[donor.id]);
      result[donor.id] -= take;
      need -= take;
      if (need === 0) break;
    }
    result[residualId] = RESIDUAL_MIN - need;
  }

  // 불변식 — 미러도 1차 방어를 반복한다. 어긋나면 조용히 그리지 않고 즉시 멈춘다.
  const values = Object.values(result);
  if (sum(values) !== 100 || values.some((v) => v % STEP !== 0)) {
    throw new Error("재분배 불변식 위반 — 서버 구현과 어긋났다");
  }
  return result;
}
