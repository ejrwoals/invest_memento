// 골든 벡터 — fixtures/probability.json 을 pytest(test_probability.py)와 같이 읽는다.
// 미러가 어긋나면 드래그 중 보이던 숫자와 저장된 숫자가 달라진다 — 가장 나쁜 종류의 버그.

import { describe, expect, it } from "vitest";
import rawCases from "../../../../fixtures/probability.json";
import { RESIDUAL_MIN, STEP, redistribute, type ScenarioProb } from "./probability";

interface GoldenCase {
  name: string;
  scenarios: ScenarioProb[];
  changed: { id: string; value: number };
  locked_ids: string[];
  expected: Record<string, number> | null;
}

const CASES = rawCases as GoldenCase[];

describe("redistribute — 골든 벡터 전 케이스", () => {
  it.each(CASES.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const result = redistribute(c.scenarios, c.changed.id, c.changed.value, new Set(c.locked_ids));
    expect(result).toEqual(c.expected);

    if (result !== null) {
      // 불변식 셋 — 합 100 · 전부 5의 배수 · residual ≥ 5
      const values = Object.values(result);
      expect(values.reduce((a, b) => a + b, 0)).toBe(100);
      for (const v of values) expect(v % STEP).toBe(0);
      const residual = c.scenarios.find((s) => s.is_residual);
      if (residual) expect(result[residual.id]).toBeGreaterThanOrEqual(RESIDUAL_MIN);
    }
  });
});

describe("redistribute — 입력 검증 (서버와 같은 거부)", () => {
  const base: ScenarioProb[] = [
    { id: "A", probability: 65, is_residual: false },
    { id: "B", probability: 25, is_residual: false },
    { id: "residual", probability: 10, is_residual: true },
  ];

  it("갈래에 없는 시나리오는 던진다", () => {
    expect(() => redistribute(base, "X", 50)).toThrow();
  });

  it("잠긴 시나리오는 움직일 수 없다", () => {
    expect(() => redistribute(base, "A", 50, new Set(["A"]))).toThrow();
  });

  it("잔여 슬롯이 둘이면 던진다", () => {
    const two = [...base, { id: "residual2", probability: 0, is_residual: true }];
    expect(() => redistribute(two, "A", 50)).toThrow();
  });
});
