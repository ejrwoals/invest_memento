"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ApiError,
  api,
  type AutoNowOut,
  type KeepOut,
  type NoteDetail,
  type ReminderDetailOut,
  type SnapshotOut,
  type ThenGalaeOut,
  type WatchNowOut,
} from "@/lib/api";
import TrendChart, { fmtVal } from "@/components/TrendChart";
import { fmtDate, timeAgoText } from "@/lib/format";
import { COMPARATOR_LABEL, DISCLAIMER, HOW_LABEL, KEEP_DONE, TAG_USER } from "@/lib/terms";

// 리마인드 상세 (ux §3.5) — ① 당시의 나(원문 그대로, P2) → ② 그동안의 일(수치만) →
// ③ 지금은?. 순서가 고정이다: 최신 정보의 프레임으로 과거를 읽지 않도록 ①이 먼저다.
// 넓은 화면은 ①② 7:5 병치(.two), 좁으면 세로 순차.

const KEEP_LEAVE_MS = 1200; // 완료 한 줄을 읽을 시간만 주고 홈으로

/** 시나리오 표식 색 — 1·2번은 고유색, 그 뒤와 residual 은 무채색 (프로토타입 .wedge) */
function wedgeColor(index: number, isResidual: boolean): string {
  if (isResidual) return "var(--w3)";
  return index === 0 ? "var(--w1)" : index === 1 ? "var(--w2)" : "var(--w3)";
}

function ThenGalae({ g }: { g: ThenGalaeOut }) {
  return (
    <div
      style={{
        marginTop: "var(--s4)",
        paddingTop: "var(--s3)",
        borderTop: "1px solid var(--line-1)",
      }}
    >
      <div style={{ fontSize: "var(--text-caption)", color: "var(--ink-2)" }}>
        {g.question}
        {g.judge_end !== null && ` · ${fmtDate(g.judge_end)}`}
      </div>
      <div
        style={{
          marginTop: 8,
          display: "flex",
          flexDirection: "column",
          gap: 6,
          fontSize: "var(--text-caption)",
          color: "var(--ink-1)",
        }}
      >
        {g.scenarios.map((s, i) => (
          <div
            key={s.id}
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: "var(--s3)",
              ...(s.is_residual ? { color: "var(--ink-2)" } : {}),
            }}
          >
            <span>
              <span className="wedge" style={{ background: wedgeColor(i, s.is_residual) }} />
              {s.name}
            </span>
            <span className="num">{s.probability !== null ? `${s.probability}%` : "—"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** 목표까지의 거리 한 줄 — `95,000까지 · 지금 92,800` */
function distanceLine(a: AutoNowOut, current: number | null): string | null {
  const now = current !== null ? ` · 지금 ${fmtVal(current)}` : "";
  if (a.met && a.met_at !== null) return `달성 ${fmtDate(a.met_at)}${now}`;
  if (a.comparator === "between" && a.target_low !== null && a.target_high !== null) {
    return `${fmtVal(Number(a.target_low))}~${fmtVal(Number(a.target_high))} 사이까지${now}`;
  }
  if (a.target_value !== null && a.comparator !== "change_pct") {
    return `${fmtVal(Number(a.target_value))}까지${now}`;
  }
  return current !== null ? `지금 ${fmtVal(current)}` : null;
}

function AutoRow({
  a,
  points,
  domainFrom,
  domainTo,
}: {
  a: AutoNowOut;
  points: SnapshotOut[] | undefined;
  domainFrom: string;
  domainTo: string | null;
}) {
  const current =
    a.current_value !== null
      ? Number(a.current_value)
      : points !== undefined && points.length > 0
        ? Number(points[points.length - 1].close)
        : null;
  const distance = distanceLine(a, current);
  return (
    <div className={a.met ? "ind__row is-hit" : "ind__row"}>
      <div className="ind__n">
        {a.series_label ?? a.scenario_name}
        <small>
          {a.scenario_name}
          {a.comparator !== null && ` · ${COMPARATOR_LABEL[a.comparator] ?? ""}`} · {HOW_LABEL.auto}
        </small>
      </div>
      {distance !== null && <div className="ind__v">{distance}</div>}
      <div className="ind__s">
        {a.met ? `달성${a.met_at !== null ? ` · ${fmtDate(a.met_at)}` : ""}` : "아직"}
      </div>
      {points !== undefined && points.length > 0 && (
        <TrendChart
          mode="spark"
          points={points}
          domainFrom={domainFrom}
          domainTo={domainTo}
          target={{
            comparator: a.comparator ?? "gte",
            value: a.target_value === null ? null : Number(a.target_value),
            low: a.target_low === null ? null : Number(a.target_low),
            high: a.target_high === null ? null : Number(a.target_high),
          }}
          metAt={a.met_at}
        />
      )}
    </div>
  );
}

function WatchRow({ w }: { w: WatchNowOut }) {
  return (
    <div className="ind__row">
      <div className="ind__n">
        {w.label}
        <small>지켜보는 수치</small>
      </div>
      <div className="ind__v">
        {w.current_value !== null ? fmtVal(Number(w.current_value)) : "—"}
      </div>
      <div className="ind__s">
        {w.current_date !== null ? fmtDate(w.current_date) : "값 없음"}
      </div>
    </div>
  );
}

export default function ReminderPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [detail, setDetail] = useState<ReminderDetailOut | null>(null);
  const [note, setNote] = useState<NoteDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [seriesMap, setSeriesMap] = useState<Record<string, SnapshotOut[]>>({});
  const [keeping, setKeeping] = useState(false);
  const [kept, setKept] = useState(false);

  useEffect(() => {
    api<ReminderDetailOut>(`/reminders/${id}`)
      .then(setDetail)
      .catch((e: unknown) => {
        setError(
          e instanceof ApiError && e.status === 404
            ? "이 리마인드를 찾을 수 없습니다. 홈에서 다시 열어 주세요."
            : "리마인드를 불러오지 못했습니다. 잠시 후 다시 열어 주세요.",
        );
      });
  }, [id]);

  // 계열 좌표(provider/code)는 노트 상세에만 있다 — 추이가 없어도 ①·② 수치는 산다
  useEffect(() => {
    if (detail === null) return;
    api<NoteDetail>(`/notes/${detail.note_id}`)
      .then(setNote)
      .catch(() => undefined);
  }, [detail]);

  const seriesOf = useMemo(() => {
    const m = new Map<string, { provider: string; code: string }>();
    if (note !== null) {
      for (const g of note.galae) {
        for (const s of g.scenarios) {
          if (s.series_provider !== null && s.series_code !== null) {
            m.set(s.id, { provider: s.series_provider, code: s.series_code });
          }
        }
      }
    }
    return m;
  }, [note]);

  const judgeEndOf = useMemo(() => {
    const m = new Map<string, string | null>();
    if (detail !== null) {
      for (const g of detail.then.galae) {
        for (const s of g.scenarios) m.set(s.id, g.judge_end);
      }
    }
    return m;
  }, [detail]);

  // 추이 데이터 — 기록 시점 → 판단 시점 구간 (spark 도메인 규칙, ux §7)
  useEffect(() => {
    if (detail === null || note === null) return;
    const from = detail.then.recorded_at.slice(0, 10);
    for (const a of detail.since.auto) {
      const coords = seriesOf.get(a.scenario_id);
      if (coords === undefined) continue;
      const params = new URLSearchParams({ from });
      const to = judgeEndOf.get(a.scenario_id) ?? null;
      if (to !== null) params.set("to", to);
      api<SnapshotOut[]>(`/series/${coords.provider}/${coords.code}?${params.toString()}`)
        .then((pts) => setSeriesMap((prev) => ({ ...prev, [a.scenario_id]: pts })))
        .catch(() => undefined); // 차트가 비어도 수치 한 줄은 산다
    }
  }, [detail, note, seriesOf, judgeEndOf]);

  // `그대로 봅니다` — 읽었고, 안 바꿨다. 확률 이력 없이 검토일만 갱신 (ux §3.5)
  const keep = async () => {
    if (keeping || detail === null) return;
    setKeeping(true);
    setActionError(null);
    try {
      await api<KeepOut>(`/reminders/${detail.id}/keep`, { method: "POST" });
      setKept(true);
      setTimeout(() => router.push("/"), KEEP_LEAVE_MS);
    } catch {
      setKeeping(false);
      setActionError("검토일을 갱신하지 못했습니다. 잠시 후 다시 눌러 주세요.");
    }
  };

  const domainFrom = detail !== null ? detail.then.recorded_at.slice(0, 10) : "";

  return (
    <main>
      <div className="appbar">
        <Link href="/" className="btn btn--quiet btn--sm">
          ← 홈
        </Link>
        {detail && (
          <div>
            <h2>{note !== null ? `${note.target_name} · 다시 보기` : "다시 보기"}</h2>
            <div className="sub">{fmtDate(detail.then.recorded_at)} 기록</div>
          </div>
        )}
      </div>
      <div className="pad">
        {error && <p className="empty">{error}</p>}
        {!error && detail === null && <p className="empty">불러오는 중…</p>}
        {detail && (
          <>
            <div className="two">
              <div>
                <div className="step">
                  <i>1</i>
                  {timeAgoText(detail.then.recorded_at)}, 당신은 이렇게 생각했습니다
                </div>
                {/* 원문 그대로 — 재요약·각색하지 않는다 (P2) */}
                <div className="card card--fixed">
                  <div style={{ fontSize: "var(--text-sm)", lineHeight: "var(--lh-long)" }}>
                    {detail.then.thesis_summary}
                  </div>
                  {detail.then.quote !== null && (
                    <div className="quote" style={{ fontSize: "var(--text-sm)" }}>
                      {detail.then.quote_authorship === "user" && (
                        <span className="tag-user">{TAG_USER}</span>
                      )}
                      “{detail.then.quote}”
                    </div>
                  )}
                  {detail.then.galae.map((g) => (
                    <ThenGalae key={g.id} g={g} />
                  ))}
                </div>
              </div>

              <div>
                <div className="step">
                  <i>2</i>그동안 수치는 이렇게 움직였습니다
                </div>
                <div className="card">
                  {detail.since.auto.length === 0 && detail.since.watches.length === 0 ? (
                    <p className="empty">
                      따라가는 수치가 없는 노트입니다. 아래에서 지금의 생각만 확인해 주세요.
                    </p>
                  ) : (
                    <div className="ind">
                      {detail.since.auto.map((a) => (
                        <AutoRow
                          key={a.scenario_id}
                          a={a}
                          points={seriesMap[a.scenario_id]}
                          domainFrom={domainFrom}
                          domainTo={judgeEndOf.get(a.scenario_id) ?? null}
                        />
                      ))}
                      {detail.since.watches.map((w) => (
                        <WatchRow key={w.watch_id} w={w} />
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="step" style={{ marginTop: "var(--s6)" }}>
              <i>3</i>지금은 어떻게 보시나요
            </div>
            <div className="card">
              {kept ? (
                <p style={{ fontSize: "var(--text-sm)" }}>{KEEP_DONE}</p>
              ) : (
                <>
                  <p
                    style={{
                      fontSize: "var(--text-caption)",
                      color: "var(--ink-2)",
                      marginBottom: "var(--s4)",
                    }}
                  >
                    다시 읽어본 지금도 같게 보시나요?
                  </p>
                  <div className="row">
                    <Link href={`/notes/${detail.note_id}/setup`} className="btn btn--primary">
                      다시 판단하기
                    </Link>
                    <button className="btn" onClick={keep} disabled={keeping}>
                      그대로 봅니다
                    </button>
                    <Link href="/" className="btn btn--quiet">
                      나중에
                    </Link>
                  </div>
                  {actionError && (
                    <p className="empty" style={{ marginTop: "var(--s3)" }}>
                      {actionError}
                    </p>
                  )}
                </>
              )}
            </div>

            <p className="disclaimer">{DISCLAIMER}</p>
          </>
        )}
      </div>
    </main>
  );
}
