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

/** target_type → 화면 표기 (초안 확인의 종류 고르기) */
export const TARGET_TYPE_LABEL: Record<string, string> = {
  ticker: "종목",
  asset: "자산",
  theme: "테마",
};

/** residual 시나리오 이름 — notes.py RESIDUAL_NAME 과 동일해야 한다 */
export const RESIDUAL_NAME = "그 외 예상 못한 전개";

/** 사용자 저작 표기 — 원문 그대로 옮긴 블록에 붙는다 */
export const TAG_USER = "[사용자]";

/** comparator → 화면 표기 (2단계 폼·수치 한 줄) */
export const COMPARATOR_LABEL: Record<string, string> = {
  gte: "이상",
  lte: "이하",
  between: "범위",
  change_pct: "변화율",
};

/** 관측 규칙 안내 한 줄 — 설정 항목이 아니라 고정 규칙이다 (ux §3.3) */
export const OBSERVATION_RULE =
  "기간 중 한 번이라도 닿으면 달성으로 보고 여쭙습니다 · 장중 포함 · 이 기준은 바꿀 수 없습니다";

/** 확률 눈금 아래 감각 라벨 — 감각과 숫자를 잇는다 (ux §6) */
export const PROBABILITY_SCALE = ["희박", "반반", "유력"] as const;

/** 지켜보는 수치 섹션 머리 — 판정하지 않는다는 사실을 붙인다 */
export const WATCH_SECTION_LABEL = "지켜보는 수치 · 판정하지 않습니다";

/** 지켜보는 수치 안내 — 목표선도 판정도 없다 */
export const WATCH_NOTE = "목표값도 달성 판정도 없이 추이만 보여드립니다";

/** 홈 피드 카드 머리 — kind → 왜 지금 떴는지 (ux §3.1·§9.4, P8) */
export const FEED_WHY: Record<string, string> = {
  pending_judgment: "결과 확인 필요",
  auto_condition_met: "설정한 조건에 닿았습니다",
  deadline: "판단 시점이 다가옵니다",
  interval: "한동안 보지 않으셨습니다",
};

/** 홈 피드 빈 상태 — 성취 문구·개수 압박 없이 문장으로 (P5) */
export const EMPTY_FEED = "지금 다시 볼 것은 없습니다.";
export const EMPTY_FEED_PROMPT = "요즘 생각하고 계신 것이 있나요?";

/** 홈 타임라인 빈 상태 */
export const EMPTY_TIMELINE =
  "아직 다가오는 판단 시점이 없습니다. 노트에 판단 시점을 정하면 여기에 놓입니다.";

/** `그대로 봅니다` 완료 한 줄 — 확률 이력 없이 검토일만 갱신되었다 (ux §3.5) */
export const KEEP_DONE = "다음 검토일을 미뤘습니다.";

/** 스트리밍 실패 시 재시도 안내 — 사용자 발화는 서버가 먼저 저장하므로 유실되지 않는다 */
export const STREAM_RETRY_NOTICE =
  "응답을 받지 못했습니다. 말씀하신 내용은 저장되어 있으니, 이어서 입력하시면 대화가 계속됩니다.";
