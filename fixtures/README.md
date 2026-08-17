# 골든 테스트 벡터

검증 규칙의 정본은 서버 Python 단일 구현이고, 유일하게 이중 구현이 허용되는 것은
확률 재분배(슬라이더 UX용 TS 미러)다. 이 디렉토리의 JSON 벡터를 Python(pytest)과
TS(vitest) 테스트가 **둘 다** 통과해야 CI가 초록이 된다.
배경: `docs/development-plan.md` §13.3, 알고리즘 명세: `docs/dev/02-backend.md` §6.

- `probability.json` — 확률 재분배 벡터 (M2에서 §3.1의 검증 사례로 채운다)

형식: `[{ "name", "scenarios": [{id, probability, is_residual}], "changed": {id, value},
"locked_ids": [], "expected": {id: value, ...} }]`
