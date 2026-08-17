// FastAPI 클라이언트 — Supabase 세션 토큰을 실어 보내는 fetch 래퍼.
// 데이터 계약의 정본은 apps/api/app/routers/notes.py 의 응답 스키마다.
import { createClient } from "@/lib/supabase/client";

// ── 응답 타입 (notes.py 스키마 미러 — 날짜는 ISO 문자열로 온다) ──────────

export interface NoteSummary {
  id: string;
  target_name: string;
  thesis_summary: string;
  color: string;
  is_complete: boolean; // 판단 시점 있는 갈래가 하나 이상
  next_judge_end: string | null; // YYYY-MM-DD
  galae_count: number;
}

export interface ScenarioOut {
  id: string;
  name: string;
  description: string | null;
  trigger_conditions: string | null;
  position: number;
  is_residual: boolean;
  status: string;
  status_reason: string | null;
  probability: number | null;
  resolution_type: string; // auto | manual | complement
  series_provider: string | null;
  series_code: string | null;
  series_label: string | null;
  comparator: string | null;
  target_value: string | null; // Decimal → 문자열
  target_low: string | null;
  target_high: string | null;
  baseline_date: string | null;
  auto_status: string | null;
  met_at: string | null;
  progress: number | null;
  marked: string | null;
  marked_at: string | null;
}

export interface GalaeOut {
  id: string;
  question: string;
  judge_kind: string | null;
  judge_start: string | null;
  judge_end: string | null;
  status: string;
  position: number;
  scenarios: ScenarioOut[];
}

export interface PremiseOut {
  id: string;
  statement: string;
  position: number;
  quoted_from: string | null;
  linked_watch_id: string | null;
}

export interface WatchOut {
  id: string;
  provider: string;
  code: string;
  label: string;
  created_at: string;
}

export interface NoteDetail {
  id: string;
  target_type: string;
  target_symbol: string | null;
  target_name: string;
  thesis_summary: string;
  thesis_detail: string | null;
  color: string;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  is_complete: boolean;
  galae: GalaeOut[];
  premises: PremiseOut[];
  watches: WatchOut[];
}

// ── 2단계 폼·차트 (series.py · notes.py 2단계 엔드포인트 미러) ────────────

export interface CatalogEntryOut {
  provider: string;
  code: string;
  label: string;
  kind: string; // stock | index | macro …
  unit: string | null;
  has_intraday: boolean;
  unregistered?: boolean; // 카탈로그 미등록 후보 — 선택하면 참조 시 자동 등록된다
}

export interface SnapshotOut {
  date: string; // YYYY-MM-DD
  close: string; // Decimal → 문자열
  high: string | null;
  low: string | null;
}

export type Comparator = "gte" | "lte" | "between" | "change_pct";

export interface ResolutionPatchBody {
  series_provider: string;
  series_code: string;
  series_label: string;
  comparator: Comparator;
  target_value: string | null;
  target_low: string | null;
  target_high: string | null;
  baseline_date: string | null;
  reason: string | null;
}

export interface ProbabilitiesPatchBody {
  changed: { scenario_id: string; value: number };
  locked_ids: string[];
  reason: string | null;
}

export interface GalaeProbabilitiesOut {
  galae_id: string;
  probabilities: { scenario_id: string; value: number }[];
}

// ── 대화형 노트 작성 (conversations.py · thesis_builder.assemble 미러) ───

export interface MessageOut {
  id: string;
  seq: number;
  role: string; // user | assistant
  content: string;
  created_at: string;
}

export interface ConversationOut {
  id: string;
  status: string; // draft | attached | abandoned
  note_id: string | null;
  draft_note: DraftPayload | null;
  messages: MessageOut[];
}

export interface ConversationSummary {
  id: string;
  status: string;
  updated_at: string;
  preview: string;
}

export type IssueSeverity = "blocking" | "ask" | "incomplete" | "notice";

export interface IssueOut {
  code: string;
  severity: IssueSeverity;
  field: string;
  message: string; // UI가 그대로 쓰는 완성된 문장
  fix: { label: string; action: string } | null;
}

// NoteDraft(domain/validation.py) 미러 — POST /notes 본문이기도 하다
export interface DraftScenario {
  name: string;
  description: string | null;
  trigger_conditions: string | null;
  is_residual: boolean;
  resolution_type: string; // auto | manual | complement
  probability: number | null;
  series_provider: string | null;
  series_code: string | null;
  series_label: string | null;
  comparator: string | null;
  target_value: string | null;
  target_low: string | null;
  target_high: string | null;
  baseline_date: string | null;
}

export interface DraftGalae {
  question: string;
  judge_kind: "date" | "range" | null;
  judge_start: string | null;
  judge_end: string | null;
  scenarios: DraftScenario[];
}

export interface DraftPremise {
  statement: string;
  quoted_from: string | null;
}

export interface NoteDraftBody {
  target_type: "ticker" | "asset" | "theme" | null;
  target_symbol: string | null;
  target_name: string;
  thesis_summary: string;
  thesis_detail: string | null;
  color: string | null;
  galae: DraftGalae[];
  premises: DraftPremise[];
}

export interface DraftQuote {
  text: string;
  quoted_from: string | null;
  authorship: "user" | "ai"; // 출처 대조 실패 시 ai 로 강등된다
}

export interface DerivedJudge {
  galae_index: number;
  source_text: string | null;
  judge_start: string | null;
  judge_end: string;
  message: string;
}

export interface DraftPayload {
  note: NoteDraftBody;
  quote: DraftQuote | null;
  derived_judges: DerivedJudge[];
  incomplete: string[]; // 내부 코드 — 화면에 그대로 올리지 않는다
}

export interface BuildOut {
  draft_note: DraftPayload;
  issues: IssueOut[];
}

// ── 홈·리마인드 (home.py 스키마 미러) ─────────────────────────────────────

export interface FeedCardOut {
  kind: string; // pending_judgment | auto_condition_met | deadline | interval
  note_id: string;
  galae_id: string | null;
  title: string;
  reason: string; // 왜 지금 이 카드가 떴는지 — 서버가 완성 문장으로 준다
  date: string | null; // YYYY-MM-DD
}

export interface TimelineEntryOut {
  judge_end: string; // YYYY-MM-DD — 오늘 이후만 온다 (지나간 것은 피드가 다룬다)
  note_id: string;
  galae_id: string;
  note_title: string;
  color: string;
  question: string;
}

export interface HomeOut {
  feed: FeedCardOut[]; // 우선순위 순 정렬 완료 — 클라이언트는 자르기만 한다
  timeline: TimelineEntryOut[]; // judge_end 오름차순
  draft_conversation_id: string | null;
}

export interface ThenScenarioOut {
  id: string;
  name: string;
  probability: number | null;
  is_residual: boolean;
}

export interface ThenGalaeOut {
  id: string;
  question: string;
  judge_end: string | null;
  scenarios: ThenScenarioOut[];
}

/** ① 당시의 나 — 원본 그대로, 재요약하지 않는다 (P2) */
export interface ThenOut {
  thesis_summary: string;
  quote: string | null;
  quote_authorship: string | null; // user | ai — user 일 때만 [사용자] 표기 (P3)
  recorded_at: string;
  galae: ThenGalaeOut[];
}

export interface AutoNowOut {
  scenario_id: string;
  scenario_name: string;
  series_label: string | null;
  comparator: string | null;
  target_value: string | null; // Decimal → 문자열
  target_low: string | null;
  target_high: string | null;
  current_value: string | null;
  current_date: string | null;
  progress: number | null;
  met: boolean;
  met_at: string | null;
}

export interface WatchNowOut {
  watch_id: string;
  label: string;
  current_value: string | null;
  current_date: string | null;
}

/** ② 그동안의 일 — 수치만. 뉴스 리서치는 M8 에서 붙는다 */
export interface SinceOut {
  auto: AutoNowOut[];
  watches: WatchNowOut[];
}

export interface ActionOut {
  note_id: string;
  note_url: string;
  keep_url: string;
}

export interface ReminderDetailOut {
  id: string;
  kind: string;
  note_id: string;
  opened_at: string;
  then: ThenOut;
  since: SinceOut;
  action: ActionOut;
}

export interface KeepOut {
  note_id: string;
  next_trigger_at: string;
}

// ── fetch 래퍼 ───────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function toLogin(): never {
  // React 밖의 API 계층이라 useRouter 를 쓸 수 없다 — 전체 리로드로 /login 이동.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.href = "/login";
  // 리다이렉트가 붙기 전에 호출부 흐름을 끊는다
  throw new ApiError(401, "로그인이 필요합니다");
}

/** 인증 헤더만 붙인 원시 fetch — SSE 스트림처럼 Response 를 직접 다뤄야 할 때 쓴다. */
export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) toLogin();

  const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}${path}`, {
    ...init,
    headers: {
      ...init?.headers,
      Authorization: `Bearer ${session.access_token}`,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
    },
  });

  if (res.status === 401) toLogin();
  return res;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text || `요청 실패 (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ── 오류 본문 해석 — 백엔드의 {detail:{code,message}} 규약에서 문장을 꺼낸다 ──

export interface ApiErrorDetail {
  code?: string;
  message?: string;
}

export function apiErrorDetail(e: unknown): ApiErrorDetail | null {
  if (!(e instanceof ApiError)) return null;
  try {
    const parsed: unknown = JSON.parse(e.message);
    if (parsed === null || typeof parsed !== "object") return null;
    const detail = (parsed as { detail?: unknown }).detail;
    if (Array.isArray(detail)) {
      // blocking Issue 목록(422) — 첫 문장만 대표로 쓴다
      const first = detail[0] as { code?: unknown; message?: unknown } | undefined;
      if (first && typeof first.message === "string") {
        return {
          code: typeof first.code === "string" ? first.code : undefined,
          message: first.message,
        };
      }
      return null;
    }
    if (detail !== null && typeof detail === "object") {
      const d = detail as { code?: unknown; message?: unknown };
      return {
        code: typeof d.code === "string" ? d.code : undefined,
        message: typeof d.message === "string" ? d.message : undefined,
      };
    }
  } catch {
    // JSON 이 아니면 내부 문자열 — 화면 문장으로 쓰지 않는다
  }
  return null;
}

/** 백엔드가 준 완성 문장이 있으면 그것을, 아니면 fallback 을 쓴다. */
export function apiErrorMessage(e: unknown, fallback: string): string {
  return apiErrorDetail(e)?.message ?? fallback;
}
