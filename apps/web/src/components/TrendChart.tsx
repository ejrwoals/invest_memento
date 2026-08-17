"use client";

// 수치 추이 — ux-design §7 정본. 한 렌더러, 두 밀도 (docs/dev/03-frontend §5.2).
// spark(축약): 띠·목표선·추이·오늘 점만. full(전체): 기록선 점선·목표선 실선·사이 띠·
// 고저 띠·오늘 이후 빗금·최초 터치 점·크로스헤어 툴팁·값 표까지.
// 추이선은 무채색이다 — 색은 화면에 점 하나뿐 (rise=가설 강화이지 가격 상승이 아니다).
// 지켜보는 수치(target 없음)는 목표 레이어를 끈 같은 렌더러다.

import { useId, useState } from "react";
import type { SnapshotOut } from "@/lib/api";
import { fmtDate } from "@/lib/format";

const VW = 640;
const VH = 150;

export interface TrendTarget {
  comparator: string; // gte | lte | between | change_pct
  value: number | null;
  low: number | null;
  high: number | null;
}

interface Point {
  date: string;
  close: number;
  high: number | null;
  low: number | null;
}

interface Props {
  mode: "spark" | "full";
  points: SnapshotOut[]; // 확정 종가만 온다 — 미마감 당일 없음 (서버 보장)
  domainFrom: string; // 기록 시점 (노트 created_at)
  domainTo: string | null; // 판단 시점 judge_end — 없으면(지켜보는 수치) 마지막 점까지
  target?: TrendTarget | null; // 없으면 목표선·띠를 그리지 않는다
  metAt?: string | null; // 최초 터치일 (서버 평가 캐시). 없으면 데이터에서 계산
  unit?: string | null;
  title?: string; // full 전용 헤더
  subtitle?: string;
}

/** 값 표기 — 큰 수는 천 단위, 작은 수(금리 등)는 소수 유지 */
export function fmtVal(v: number, unit?: string | null): string {
  const text =
    Math.abs(v) >= 1000
      ? Math.round(v).toLocaleString("ko-KR")
      : (Math.round(v * 100) / 100).toLocaleString("ko-KR", { maximumFractionDigits: 2 });
  return unit ? `${text}${unit}` : text;
}

/** MM.DD — 좁은 라벨용 */
function fmtShort(iso: string): string {
  return iso.slice(5, 10).replace("-", ".");
}

function dayOf(iso: string): number {
  return Date.parse(`${iso.slice(0, 10)}T00:00:00Z`) / 86_400_000;
}

/** 그날 도달 범위가 목표에 닿았는가 — 서버 evaluate.touched 와 같은 규칙 */
function touchedOn(p: Point, t: TrendTarget): boolean {
  const lo = p.low ?? p.close;
  const hi = p.high ?? p.close;
  if (t.comparator === "gte") return t.value !== null && hi >= t.value;
  if (t.comparator === "lte") return t.value !== null && lo <= t.value;
  if (t.comparator === "between") {
    return t.low !== null && t.high !== null && lo <= t.high && hi >= t.low;
  }
  return false; // change_pct 는 기준값이 필요해 클라이언트에서 판정하지 않는다
}

export default function TrendChart({
  mode,
  points: rawPoints,
  domainFrom,
  domainTo,
  target = null,
  metAt = null,
  unit = null,
  title,
  subtitle,
}: Props) {
  const patternId = useId();
  const [hover, setHover] = useState<{ x: number; y: number; date: string; close: number } | null>(
    null,
  );

  const points: Point[] = rawPoints.map((p) => ({
    date: p.date,
    close: Number(p.close),
    high: p.high === null ? null : Number(p.high),
    low: p.low === null ? null : Number(p.low),
  }));

  if (points.length === 0) {
    return <p className="empty">아직 그릴 수치가 없습니다. 다음 마감 후에 채워집니다.</p>;
  }

  const last = points[points.length - 1];
  const baseline = points[0].close; // 기록 시점 값 — 점선은 과거의 기준

  // ── 좌표 — 도메인은 기록 시점 → 판단 시점으로 고정 (줌·기간 변경 없음) ──
  const d0 = dayOf(domainFrom);
  const d1 = Math.max(dayOf(domainTo ?? last.date), d0 + 1);
  const x = (iso: string) => Math.max(0, Math.min(VW, ((dayOf(iso) - d0) / (d1 - d0)) * VW));

  const targetLines: number[] =
    target === null
      ? []
      : target.comparator === "between"
        ? [target.low, target.high].filter((v): v is number => v !== null)
        : target.value !== null && target.comparator !== "change_pct"
          ? [target.value]
          : [];

  const values = [
    baseline,
    ...targetLines,
    ...points.flatMap((p) => [p.close, p.high ?? p.close, p.low ?? p.close]),
  ];
  const vMin = Math.min(...values);
  const vMax = Math.max(...values);
  const pad = (vMax - vMin || Math.abs(vMax) || 1) * 0.12;
  const y = (v: number) => ((vMax + pad - v) / (vMax + pad - (vMin - pad))) * VH;

  const closePath = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.date)},${y(p.close)}`).join(" ");
  const hasBand = points.some((p) => p.high !== null && p.low !== null);
  const bandPoly = hasBand
    ? [
        ...points.map((p) => `${x(p.date)},${y(p.high ?? p.close)}`),
        ...[...points].reverse().map((p) => `${x(p.date)},${y(p.low ?? p.close)}`),
      ].join(" ")
    : null;

  // ── 달성 — met_at(서버 캐시) 우선, 없으면 관측 규칙대로 데이터에서 찾는다 ──
  const metPoint =
    target === null
      ? null
      : (metAt !== null ? points.find((p) => p.date === metAt) : null) ??
        points.find((p) => touchedOn(p, target)) ??
        null;
  const met = metPoint !== undefined && metPoint !== null;
  const metY =
    metPoint === null || target === null
      ? 0
      : y(
          target.comparator === "lte"
            ? (metPoint.low ?? metPoint.close)
            : (metPoint.high ?? metPoint.close),
        );

  const lastX = x(last.date);
  const primaryTarget = targetLines.length > 0 ? targetLines[0] : null;

  const svg = (
    <svg
      viewBox={`0 0 ${VW} ${VH}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={
        title !== undefined
          ? `${title} 추이. 상세 값은 표 참조.`
          : `추이 그림 · 지금 ${fmtVal(last.close, unit)}`
      }
      onMouseMove={
        mode === "full"
          ? (e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const vx = ((e.clientX - rect.left) / rect.width) * VW;
              let best = points[0];
              for (const p of points) {
                if (Math.abs(x(p.date) - vx) < Math.abs(x(best.date) - vx)) best = p;
              }
              setHover({ x: x(best.date), y: y(best.close), date: best.date, close: best.close });
            }
          : undefined
      }
      onMouseLeave={mode === "full" ? () => setHover(null) : undefined}
    >
      {/* 기록선과 목표선 사이 = 가야 했던 거리 */}
      {primaryTarget !== null && (
        <rect
          x="0"
          y={Math.min(y(primaryTarget), y(baseline))}
          width={VW}
          height={Math.abs(y(primaryTarget) - y(baseline))}
          fill="var(--paper-2)"
          opacity=".55"
        />
      )}
      {/* 오늘 이후 = 남은 기간 (빗금) */}
      {lastX < VW - 1 && (
        <>
          <defs>
            <pattern
              id={patternId}
              width="6"
              height="6"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <line x1="0" y1="0" x2="0" y2="6" stroke="var(--line-0)" strokeWidth="2" />
            </pattern>
          </defs>
          <rect x={lastX} y="0" width={VW - lastX} height={VH} fill={`url(#${patternId})`} />
        </>
      )}
      {/* 목표선 (실선 = 지금 유효한 기준) */}
      {targetLines.map((v) => (
        <line
          key={v}
          x1="0"
          y1={y(v)}
          x2={VW}
          y2={y(v)}
          stroke="var(--line-1)"
          strokeWidth="1"
          vectorEffect="non-scaling-stroke"
        />
      ))}
      {/* 기록선 (점선 = 과거의 기준) — 목표가 있을 때만 그린다 */}
      {target !== null && (
        <line
          x1="0"
          y1={y(baseline)}
          x2={VW}
          y2={y(baseline)}
          stroke="var(--line-1)"
          strokeWidth="1"
          strokeDasharray="3 4"
          vectorEffect="non-scaling-stroke"
        />
      )}
      {/* 그날의 고가~저가 — 장중 터치 판정을 설명한다 (필수) */}
      {bandPoly !== null && <polygon points={bandPoly} fill="var(--ink-3)" opacity=".38" />}
      {/* 종가 추이 — 데이터일 뿐 신호가 아니다. 무채색 */}
      <path
        d={closePath}
        fill="none"
        stroke="var(--ink-2)"
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      {/* 목표에 처음 닿은 날 — 유일한 색. 종가까지 세로선으로 잇는다 (full) */}
      {mode === "full" && met && metPoint !== null && (
        <>
          <line
            x1={x(metPoint.date)}
            y1={metY}
            x2={x(metPoint.date)}
            y2={y(metPoint.close)}
            stroke="var(--ink-2)"
            strokeWidth="2"
            vectorEffect="non-scaling-stroke"
            opacity=".7"
          />
          <circle
            cx={x(metPoint.date)}
            cy={metY}
            r="5"
            fill="var(--rise)"
            stroke="var(--paper-0)"
            strokeWidth="2"
            vectorEffect="non-scaling-stroke"
          />
        </>
      )}
      {/* 오늘 — 달성이면 spark 는 채운 색 점, full 은 빈 원 (강조는 하나뿐) */}
      {mode === "spark" && met ? (
        <circle
          cx={lastX}
          cy={y(last.close)}
          r="6"
          fill="var(--rise)"
          stroke="var(--paper-0)"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />
      ) : (
        <circle
          cx={lastX}
          cy={y(last.close)}
          r={mode === "spark" ? 6 : 4.5}
          fill={met || target === null ? "var(--paper-0)" : "var(--rise)"}
          stroke={met || target === null ? "var(--ink-1)" : "var(--paper-0)"}
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />
      )}
      {/* 크로스헤어 (full) */}
      {mode === "full" && hover !== null && (
        <line
          x1={hover.x}
          y1="0"
          x2={hover.x}
          y2={VH}
          stroke="var(--line-1)"
          strokeWidth="1"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );

  const labels = (
    <>
      {primaryTarget !== null && (
        <span className="ilab" style={{ left: 0, top: `${(y(primaryTarget) / VH) * 100}%` }}>
          목표 {fmtVal(primaryTarget, unit)}
        </span>
      )}
      {target !== null ? (
        <span className="ilab" style={{ left: 0, top: `${(y(baseline) / VH) * 100}%` }}>
          기록 {fmtVal(baseline, unit)}
        </span>
      ) : (
        <>
          {/* 지켜보는 수치 — 목표 없이 기록·지금만 */}
          <span className="ilab" style={{ left: 0, top: `${(y(baseline) / VH) * 100}%` }}>
            기록 {fmtVal(baseline, unit)}
          </span>
          <span
            className="ilab"
            style={{
              right: 0,
              top: `${(y(last.close) / VH) * 100}%`,
              transform: "translate(0,-150%)",
            }}
          >
            지금 {fmtVal(last.close, unit)}
          </span>
        </>
      )}
      {mode === "full" && met && metPoint !== null && (
        <span
          className="ilab ilab--cross"
          style={{ left: `${(x(metPoint.date) / VW) * 100}%`, top: "11%" }}
        >
          {fmtShort(metPoint.date)} 달성
        </span>
      )}
    </>
  );

  const foot = (
    <>
      <span>{fmtDate(domainFrom)} · 기록</span>
      <span>{domainTo !== null ? `${fmtDate(domainTo)} · 판단 시점` : "오늘"}</span>
    </>
  );

  if (mode === "spark") {
    return (
      <div className="spark">
        <div className="spark__plot">
          {svg}
          {labels}
        </div>
        <div className="spark__foot">{foot}</div>
      </div>
    );
  }

  // ── 전체형 — 헤더·범례·달성 사유·값 표까지 ──
  return (
    <div className={hover !== null ? "ichart is-hot" : "ichart"}>
      {(title !== undefined || subtitle !== undefined) && (
        <div className="ichart__h">
          <div className="ichart__t">
            {title}
            {subtitle !== undefined && <small>{subtitle}</small>}
          </div>
          <div className="ichart__now">
            {fmtVal(last.close, unit)}
            <span>
              {fmtShort(last.date)} 종가
              {met && metPoint !== null ? ` · ${fmtShort(metPoint.date)} 달성` : ""}
            </span>
          </div>
        </div>
      )}
      <div className="ichart__plot">
        {svg}
        {labels}
        {hover !== null && (
          <div
            className="tip"
            style={{
              left: `${(hover.x / VW) * 100}%`,
              top: `calc(${(hover.y / VH) * 100}% - 10px)`,
            }}
          >
            {fmtShort(hover.date)} {fmtVal(hover.close, unit)}
          </div>
        )}
      </div>
      <div className="ichart__foot">{foot}</div>
      <div className="ichart__legend">
        <span>
          <i style={{ background: "var(--ink-2)", height: 2 }} />
          종가
        </span>
        {met && (
          <span>
            <i
              style={{ background: "var(--rise)", width: 10, height: 10, borderRadius: "50%" }}
            />
            목표에 처음 닿은 날
          </span>
        )}
        {hasBand && (
          <span>
            <i style={{ background: "var(--ink-3)", opacity: 0.4, height: 10, borderRadius: 2 }} />
            그날의 고가~저가
          </span>
        )}
        {primaryTarget !== null && (
          <span>
            <i style={{ background: "var(--paper-2)", height: 10, borderRadius: 2 }} />
            가야 했던 거리
          </span>
        )}
        <span>
          <i
            style={{
              background:
                "repeating-linear-gradient(45deg,var(--line-0) 0 2px,transparent 2px 5px)",
              height: 10,
              borderRadius: 2,
            }}
          />
          남은 기간
        </span>
      </div>
      {met && metPoint !== null && metPoint.high !== null && (
        <p
          style={{
            marginTop: "var(--s3)",
            paddingTop: "var(--s3)",
            borderTop: "1px solid var(--line-0)",
            fontSize: "var(--text-caption)",
            color: "var(--ink-1)",
            maxWidth: "70ch",
          }}
        >
          <b style={{ fontWeight: 600 }}>{fmtShort(metPoint.date)}</b>에 장중 값이{" "}
          {fmtVal(metPoint.high, unit)}까지 닿아 그날을 달성일로 기록했습니다. 그날 종가는{" "}
          {fmtVal(metPoint.close, unit)}
          {target !== null && primaryTarget !== null && metPoint.close < primaryTarget
            ? "이었지만, 한 번이라도 닿으면 달성이 기준입니다."
            : "입니다."}
        </p>
      )}
      <details>
        <summary>값으로 보기</summary>
        <table>
          <thead>
            <tr>
              <th>시점</th>
              {hasBand && <th>고가</th>}
              <th>종가</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p) => (
              <tr key={p.date}>
                <td>
                  {fmtDate(p.date)}
                  {metPoint !== null && p.date === metPoint.date && <b> · 달성</b>}
                </td>
                {hasBand && <td>{p.high !== null ? fmtVal(p.high, unit) : "—"}</td>}
                <td>{fmtVal(p.close, unit)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
