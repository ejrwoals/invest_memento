// 날짜 표기 헬퍼 — 화면 문구 규칙(ux §9.3)에 따라 D-day 표기는 쓰지 않는다.

/** "2026-12-31" | ISO datetime → "2026.12.31" */
export function fmtDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${y}.${m}.${d}`;
}

/** 다음 판단 시점을 사람 말로 — 오늘/N일 후/날짜. 지난 시점은 할 일로 말한다. */
export function judgeDueText(iso: string): string {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = new Date(`${iso.slice(0, 10)}T00:00:00`);
  const days = Math.round((due.getTime() - today.getTime()) / 86_400_000);
  if (days < 0) return "결과 확인 필요";
  if (days === 0) return "다음 판단 시점 오늘";
  if (days <= 30) return `다음 판단 시점 ${days}일 후`;
  return `다음 판단 시점 ${fmtDate(iso)}`;
}

/** 판단 시점이 임박했는가 — 목록의 초읽기 색 표기용 */
export function isDueSoon(iso: string): boolean {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = new Date(`${iso.slice(0, 10)}T00:00:00`);
  const days = Math.round((due.getTime() - today.getTime()) / 86_400_000);
  return days >= 0 && days <= 7;
}
