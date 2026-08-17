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

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
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
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text || `요청 실패 (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
