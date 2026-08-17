"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ApiError,
  api,
  type GalaeOut,
  type NoteDetail,
  type ScenarioOut,
  type SnapshotOut,
  type WatchOut,
} from "@/lib/api";
import { fmtDate } from "@/lib/format";
import TrendChart, { fmtVal } from "@/components/TrendChart";
import {
  COMPARATOR_LABEL,
  DISCLAIMER,
  GALAE_HEADING_SUFFIX,
  HOW_CLASS,
  HOW_LABEL,
  HOW_NOTE,
  INCOMPLETE_NOTICE,
  RESIDUAL_NOTE,
  WATCH_NOTE,
  WATCH_SECTION_LABEL,
} from "@/lib/terms";

// 노트 상세 (ux §3.4) — 나의 사고가 본문. 가설 → 성립 조건 → 갈래 블록.
// auto 시나리오 카드에는 목표까지의 거리 + 축약 추이(spark), 지켜보는 수치는
// 목표선 없는 spark 를 붙인다 (ux §7 축약형). 새 정보 서랍·도넛은 다음 단계.

type SeriesMap = Record<string, SnapshotOut[]>;

function seriesKey(provider: string, code: string): string {
  return `${provider}/${code}`;
}

/** 목표까지의 거리 한 줄 — `95,000까지 · 지금 92,800` */
function distanceLine(s: ScenarioOut, points: SnapshotOut[] | undefined): string | null {
  if (points === undefined || points.length === 0) return null;
  const now = Number(points[points.length - 1].close);
  if (s.met_at !== null) return `달성 ${fmtDate(s.met_at)} · 지금 ${fmtVal(now)}`;
  if (s.comparator === "between" && s.target_low !== null && s.target_high !== null) {
    return `${fmtVal(Number(s.target_low))}~${fmtVal(Number(s.target_high))} 사이까지 · 지금 ${fmtVal(now)}`;
  }
  if (s.target_value !== null && s.comparator !== "change_pct") {
    return `${fmtVal(Number(s.target_value))}까지 · 지금 ${fmtVal(now)}`;
  }
  return `지금 ${fmtVal(now)}`;
}

function ScenarioCard({
  s,
  index,
  series,
  domainFrom,
  domainTo,
}: {
  s: ScenarioOut;
  index: number;
  series: SnapshotOut[] | undefined;
  domainFrom: string;
  domainTo: string | null;
}) {
  const note = s.is_residual ? RESIDUAL_NOTE : HOW_NOTE[s.resolution_type];
  const isAuto = s.resolution_type === "auto" && s.series_provider !== null;
  const distance = isAuto ? distanceLine(s, series) : null;
  return (
    <article className={s.is_residual ? "branch branch--far" : "branch"}>
      <div className="branch__h">
        <div>
          <div className="branch__no">{String(index + 1).padStart(2, "0")}</div>
          <h3 className="branch__t">{s.name}</h3>
        </div>
        <span className={`how ${HOW_CLASS[s.resolution_type] ?? "how--manual"}`}>
          {HOW_LABEL[s.resolution_type] ?? "직접 확인"}
        </span>
      </div>
      {s.description && <p className="branch__d">{s.description}</p>}
      {s.probability !== null && (
        <div className="prob">
          <div className="prob__v" style={s.is_residual ? { color: "var(--ink-2)" } : undefined}>
            {s.probability}%<small>안팎</small>
          </div>
        </div>
      )}
      {isAuto && (
        <div className="ind">
          <div className={s.met_at !== null ? "ind__row is-hit" : "ind__row"}>
            <div className="ind__n">
              {s.series_label ?? s.series_code}
              <small>
                {COMPARATOR_LABEL[s.comparator ?? ""] ?? ""} · 장중 한 번이라도 닿으면 달성
              </small>
            </div>
            {distance !== null && <div className="ind__v">{distance}</div>}
            <div className="ind__s">{s.met_at !== null ? `달성 · ${fmtDate(s.met_at)}` : "아직"}</div>
            {series !== undefined && series.length > 0 && s.series_provider !== null && (
              <TrendChart
                mode="spark"
                points={series}
                domainFrom={domainFrom}
                domainTo={domainTo}
                target={{
                  comparator: s.comparator ?? "gte",
                  value: s.target_value === null ? null : Number(s.target_value),
                  low: s.target_low === null ? null : Number(s.target_low),
                  high: s.target_high === null ? null : Number(s.target_high),
                }}
                metAt={s.met_at}
              />
            )}
          </div>
        </div>
      )}
      {note && (
        <div className="branch__meta">
          <span>{note}</span>
        </div>
      )}
    </article>
  );
}

function GalaeBlock({
  g,
  index,
  seriesMap,
  domainFrom,
}: {
  g: GalaeOut;
  index: number;
  seriesMap: SeriesMap;
  domainFrom: string;
}) {
  const scenarios = [...g.scenarios].sort((a, b) => a.position - b.position);
  return (
    <>
      <div className="sec-label" style={{ marginTop: "var(--s6)" }}>
        갈래 {index + 1} · {GALAE_HEADING_SUFFIX}
      </div>
      <div className="galae">
        <div className="galae__q">
          <h3>{g.question}</h3>
          <div className="galae__when">
            {g.judge_end !== null ? (
              <span>{fmtDate(g.judge_end)}에 답이 나옵니다</span>
            ) : (
              <span>{INCOMPLETE_NOTICE}</span>
            )}
          </div>
        </div>
        <div className="galae__body">
          {scenarios.map((s, i) => (
            <ScenarioCard
              key={s.id}
              s={s}
              index={i}
              series={
                s.series_provider !== null && s.series_code !== null
                  ? seriesMap[seriesKey(s.series_provider, s.series_code)]
                  : undefined
              }
              domainFrom={domainFrom}
              domainTo={g.judge_end}
            />
          ))}
        </div>
      </div>
    </>
  );
}

function WatchRow({
  w,
  series,
  domainFrom,
}: {
  w: WatchOut;
  series: SnapshotOut[] | undefined;
  domainFrom: string;
}) {
  const first = series !== undefined && series.length > 0 ? Number(series[0].close) : null;
  const last =
    series !== undefined && series.length > 0 ? Number(series[series.length - 1].close) : null;
  return (
    <div className="ind__row">
      <div className="ind__n">
        {w.label}
        <small>
          {w.provider} · {w.code}
        </small>
      </div>
      {first !== null && last !== null && (
        <div className="ind__v">
          {fmtVal(first)} → {fmtVal(last)}
        </div>
      )}
      <div className="ind__s">추이만</div>
      {series !== undefined && series.length > 0 && (
        // 지켜보는 수치 — 목표선 없이 추이만 (같은 렌더러에서 목표 레이어를 끈다)
        <TrendChart mode="spark" points={series} domainFrom={domainFrom} domainTo={null} />
      )}
    </div>
  );
}

export default function NoteDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [note, setNote] = useState<NoteDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [seriesMap, setSeriesMap] = useState<SeriesMap>({});

  useEffect(() => {
    api<NoteDetail>(`/notes/${id}`)
      .then(setNote)
      .catch((e: unknown) => {
        setError(
          e instanceof ApiError && e.status === 404
            ? "이 노트를 찾을 수 없습니다. 목록에서 다시 열어 주세요."
            : "노트를 불러오지 못했습니다. 잠시 후 다시 열어 주세요.",
        );
      });
  }, [id]);

  // 추이 데이터 — auto 조건·지켜보는 수치가 참조하는 계열만, 기록→판단 시점 구간으로
  useEffect(() => {
    if (note === null) return;
    const from = note.created_at.slice(0, 10);
    const wanted = new Map<string, { provider: string; code: string; to: string | null }>();
    for (const g of note.galae) {
      for (const s of g.scenarios) {
        if (s.series_provider !== null && s.series_code !== null) {
          wanted.set(seriesKey(s.series_provider, s.series_code), {
            provider: s.series_provider,
            code: s.series_code,
            to: g.judge_end,
          });
        }
      }
    }
    for (const w of note.watches) {
      wanted.set(seriesKey(w.provider, w.code), { provider: w.provider, code: w.code, to: null });
    }
    for (const [key, { provider, code, to }] of wanted) {
      const params = new URLSearchParams({ from });
      if (to !== null) params.set("to", to);
      api<SnapshotOut[]>(`/series/${provider}/${code}?${params.toString()}`)
        .then((points) => setSeriesMap((prev) => ({ ...prev, [key]: points })))
        .catch(() => undefined); // 차트가 비어도 노트 본문은 산다
    }
  }, [note]);

  const galae = note ? [...note.galae].sort((a, b) => a.position - b.position) : [];
  const premises = note ? [...note.premises].sort((a, b) => a.position - b.position) : [];
  const domainFrom = note !== null ? note.created_at.slice(0, 10) : "";

  return (
    <main>
      <div className="appbar">
        <Link href="/notes" className="btn btn--quiet btn--sm">
          ← 노트
        </Link>
        {note && (
          <div>
            <h2>{note.target_name}</h2>
            <div className="sub">{fmtDate(note.created_at)} 기록</div>
          </div>
        )}
      </div>
      <div className="pad">
        {error && <p className="empty">{error}</p>}
        {!error && note === null && <p className="empty">불러오는 중…</p>}
        {note && (
          <>
            <div className="sec-label">나의 가설</div>
            <div className="thesis">
              {note.thesis_summary}
              {note.thesis_detail && <p className="thesis__detail">{note.thesis_detail}</p>}
            </div>

            {premises.length > 0 && (
              <>
                <div className="sec-label" style={{ marginTop: "var(--s6)" }}>
                  이 판단이 성립하려면
                </div>
                <div className="card">
                  <ol className="premise">
                    {premises.map((p) => (
                      <li key={p.id}>{p.statement}</li>
                    ))}
                  </ol>
                </div>
              </>
            )}

            {galae.map((g, i) => (
              <GalaeBlock key={g.id} g={g} index={i} seriesMap={seriesMap} domainFrom={domainFrom} />
            ))}

            {note.watches.length > 0 && (
              <>
                <div className="sec-label" style={{ marginTop: "var(--s6)" }}>
                  {WATCH_SECTION_LABEL}
                </div>
                <div className="card">
                  <div className="ind">
                    {note.watches.map((w) => (
                      <WatchRow
                        key={w.id}
                        w={w}
                        series={seriesMap[seriesKey(w.provider, w.code)]}
                        domainFrom={domainFrom}
                      />
                    ))}
                  </div>
                  <div className="hint" style={{ marginTop: "var(--s3)" }}>
                    {WATCH_NOTE}
                  </div>
                </div>
              </>
            )}

            <div className="row" style={{ marginTop: "var(--s5)" }}>
              <Link href={`/notes/${note.id}/setup`} className="btn">
                확률·확인 방법 정하기
              </Link>
            </div>

            <p className="disclaimer">{DISCLAIMER}</p>
          </>
        )}
      </div>
    </main>
  );
}
