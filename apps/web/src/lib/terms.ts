// 내부 식별자 → 화면 표기 대응표 (ux-design §9.4, P8).
// 내부 용어가 화면 문자열로 나가는 유일한 통로다 — 컴포넌트가 식별자를 직접
// 한국어로 번역하는 것을 금지한다. 표시하지 않는 값은 여기에 키 자체를 두지 않는다.

/** 판정 방법 배지 표기 — resolution_type → 화면 문구 */
export const HOW_LABEL: Record<string, string> = {
  auto: "자동 확인",
  manual: "직접 확인",
  complement: "나머지",
};

/** 판정 방법 배지 스타일 클래스 (프로토타입 .how--*) */
export const HOW_CLASS: Record<string, string> = {
  auto: "how--auto",
  manual: "how--manual",
  complement: "how--comp",
};

/** 시나리오 카드 하단 설명 — 판정 방법이 무엇을 뜻하는지 사용자 말로 */
export const HOW_NOTE: Record<string, string> = {
  manual: "수치로 확인할 수 없습니다 · 판단 시점이 오면 여쭙겠습니다",
  complement: "다른 답이 아니면 이 답입니다",
};

/** residual 시나리오 카드 하단 설명 */
export const RESIDUAL_NOTE = "지울 수 없습니다";

/** 갈래 블록 머리 — 시나리오들이 배타적임을 알린다 */
export const GALAE_HEADING_SUFFIX = "하나만 일어납니다";

/** 판단 시점 미설정 노트 안내 (목록·상세 공용) */
export const INCOMPLETE_NOTICE = "판단 시점을 정하면 리마인드가 시작됩니다";

/** 노트 목록 빈 상태 — 왜 비었는지만 말한다. 개수 압박·성취 문구 금지 (P5) */
export const EMPTY_NOTES = "아직 기록한 노트가 없습니다. 첫 노트는 AI와의 대화로 만들어집니다.";

/** 면책 고지 한 줄 */
export const DISCLAIMER = "최종 투자 판단과 그 책임은 사용자 본인에게 있습니다.";
