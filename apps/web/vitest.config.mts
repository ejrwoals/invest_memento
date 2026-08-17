// vitest — 확률 재분배 미러의 골든 벡터 검증 전용 (docs/dev/03-frontend §6).
// 컴포넌트 테스트 러너가 아니다 — 환경은 node, 대상은 순수 함수뿐이다.
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
