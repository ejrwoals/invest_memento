"use client";

// 2단계 확인 방법 설정 (ux §3.3) — 대화가 아니라 폼이고, 빈 폼이 아니라 채워진 폼이다.
// 갈래마다 ① 확률 배분(슬라이더 — TS 미러로 즉시 재계산, 확정 시 갈래 단위 PATCH)
// ② auto 조건(질문이 수치형일 때만 — 계열 검색·comparator·목표값)
// ③ 지켜보는 수치(판정하지 않는다). 여집합 답과 residual 은 조건 화면에 나오지 않는다.
// `나중에 하기`가 1급 선택지다 — 건너뛰어도 노트는 정상이다.

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ApiError,
  api,
  apiErrorMessage,
  type CatalogEntryOut,
  type Comparator,
  type GalaeOut,
  type GalaeProbabilitiesOut,
  type NoteDetail,
  type ResolutionPatchBody,
  type ScenarioOut,
  type WatchOut,
} from "@/lib/api";
import { redistribute, RESIDUAL_MIN } from "@/lib/probability";
import { fmtDate } from "@/lib/format";
import {
  COMPARATOR_LABEL,
  DISCLAIMER,
  HOW_LABEL,
  OBSERVATION_RULE,
  PROBABILITY_SCALE,
  WATCH_NOTE,
  WATCH_SECTION_LABEL,
} from "@/lib/terms";

const WEDGE = ["var(--w1)", "var(--w2)"];

function wedgeColor(index: number, isResidual: boolean): string {
  return isResidual ? "var(--w3)" : WEDGE[index % WEDGE.length];
}

// ── 계열 검색 — 조건 설정과 지켜보는 수치가 같은 위젯을 쓴다 ────────────────

function SeriesSearch({
  onPick,
  placeholder,
}: {
  onPick: (entry: CatalogEntryOut) => void;
  placeholder: string;
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<CatalogEntryOut[] | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const search = useCallback((needle: string) => {
    if (timer.current !== null) clearTimeout(timer.current);
    if (!needle.trim()) {
      setResults(null);
      return;
    }
    timer.current = setTimeout(() => {
      api<CatalogEntryOut[]>(`/series/search?q=${encodeURIComponent(needle.trim())}`)
        .then(setResults)
        .catch(() => setResults([]));
    }, 300);
  }, []);

  return (
    <div>
      <input
        className="cond-form-input"
        style={{
          minHeight: 44,
          padding: "0 var(--s3)",
          border: "1px solid var(--line-1)",
          borderRadius: "var(--r2)",
          background: "var(--paper-0)",
          color: "var(--ink-0)",
          font: "inherit",
          fontSize: "var(--text-sm)",
          width: "100%",
        }}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          search(e.target.value);
        }}
        placeholder={placeholder}
        aria-label={placeholder}
      />
      {results !== null && (
        <div className="series-pick" style={{ marginTop: 8 }}>
          {results.length === 0 && (
            <p className="empty" style={{ padding: "8px var(--s3)" }}>
              찾은 계열이 없습니다
            </p>
          )}
          {results.map((r) => (
            <button
              key={`${r.provider}/${r.code}`}
              type="button"
              onClick={() => {
                onPick(r);
                setQ("");
                setResults(null);
              }}
            >
              <span>
                {r.label}
                {r.unregistered && !r.label.includes("새 종목") ? " (새 종목)" : ""}
              </span>
              <span className="mono">
                {r.provider} · {r.code}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── ① 확률 배분 — 로컬은 TS 미러, 확정만 서버 (docs/dev/03-frontend §6) ────

function ProbabilitySplitter({ galae }: { galae: GalaeOut }) {
  const scenarios = [...galae.scenarios].sort((a, b) => a.position - b.position);
  const [values, setValues] = useState<Record<string, number | null>>(() =>
    Object.fromEntries(scenarios.map((s) => [s.id, s.probability])),
  );
  const [locked, setLocked] = useState<ReadonlySet<string>>(new Set());
  const [lastChanged, setLastChanged] = useState<{ id: string; value: number } | null>(null);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  if (scenarios.length < 2) {
    // 혼자인 답에는 확률 UI 를 비활성이 아니라 없앤다 (ux §3.4)
    return <p className="empty">확률은 반대 시나리오가 생긴 뒤에 나눕니다.</p>;
  }

  const move = (id: string, raw: number) => {
    const input = scenarios.map((s) => ({
      id: s.id,
      probability: values[s.id] ?? null,
      is_residual: s.is_residual,
    }));
    let next: Record<string, number> | null;
    try {
      next = redistribute(input, id, raw, locked);
    } catch {
      return; // 잠긴 슬라이더는 disabled — 여기 오면 무시가 안전하다
    }
    if (next === null) return;
    setValues(next);
    setLastChanged({ id, value: next[id] });
    setNotice(null);
  };

  const toggleLock = (id: string) => {
    setLocked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const confirm = async () => {
    if (lastChanged === null || saving) return;
    setSaving(true);
    setNotice(null);
    try {
      // 미러 결과가 아니라 changed + locked 만 보낸다 — 서버 재분배가 정본이다
      const out = await api<GalaeProbabilitiesOut>(`/galae/${galae.id}/probabilities`, {
        method: "PATCH",
        body: JSON.stringify({
          changed: { scenario_id: lastChanged.id, value: lastChanged.value },
          locked_ids: [...locked],
          reason: reason.trim() || null,
        }),
      });
      setValues(Object.fromEntries(out.probabilities.map((p) => [p.scenario_id, p.value])));
      setLastChanged(null);
      setReason("");
      setNotice("담아 두었습니다.");
    } catch (e: unknown) {
      setNotice(apiErrorMessage(e, "저장하지 못했습니다. 잠시 후 다시 시도해 주세요."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card" style={{ marginBottom: 10 }}>
      <div className="sec-label" style={{ marginBottom: "var(--s2)" }}>
        어떻게 나눠 보시나요 · 눈금 하나가 5%
      </div>
      {scenarios.map((s, i) => {
        const isLocked = locked.has(s.id);
        return (
          <div
            key={s.id}
            className={s.is_residual ? "split-row split-row--lock" : "split-row"}
          >
            <span className="split-row__n">
              <span className="wedge" style={{ background: wedgeColor(i, s.is_residual) }} />
              {String(i + 1).padStart(2, "0")} {s.name}
            </span>
            <input
              type="range"
              className="slider"
              min={s.is_residual ? RESIDUAL_MIN : 0}
              max={95}
              step={5}
              value={values[s.id] ?? 0}
              disabled={isLocked}
              onChange={(e) => move(s.id, Number(e.target.value))}
              aria-label={`${s.name} 확률`}
            />
            <button
              type="button"
              className="lock-toggle"
              aria-pressed={isLocked}
              onClick={() => toggleLock(s.id)}
              title={isLocked ? "고정 풀기" : "고정하기"}
            >
              {isLocked ? "고정됨" : "고정"}
            </button>
            <span className="split-row__v">
              {values[s.id] !== null && values[s.id] !== undefined ? `${values[s.id]}%` : "—"}
            </span>
          </div>
        );
      })}
      <div className="scale" aria-hidden="true">
        {PROBABILITY_SCALE.map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>
      <div className="row" style={{ marginTop: "var(--s3)" }}>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="무엇을 보고 이렇게 나눴는지 (선택)"
          aria-label="확률을 바꾼 이유"
          style={{
            flex: 1,
            minWidth: 200,
            minHeight: 44,
            padding: "0 var(--s3)",
            border: "1px solid var(--line-1)",
            borderRadius: "var(--r2)",
            background: "var(--paper-0)",
            color: "var(--ink-0)",
            font: "inherit",
            fontSize: "var(--text-sm)",
          }}
        />
        <button
          type="button"
          className="btn"
          onClick={confirm}
          disabled={saving || lastChanged === null}
        >
          {saving ? "담는 중…" : "이 배분으로 확정"}
        </button>
      </div>
      {notice && (
        <p className="empty" style={{ marginTop: "var(--s2)" }}>
          {notice}
        </p>
      )}
    </div>
  );
}

// ── ② auto 조건 — 질문이 수치형일 때만 (auto 로 저장된 답에만 나온다) ───────

function ConditionEditor({ scenario, index }: { scenario: ScenarioOut; index: number }) {
  const [series, setSeries] = useState({
    provider: scenario.series_provider,
    code: scenario.series_code,
    label: scenario.series_label,
  });
  const [picking, setPicking] = useState(scenario.series_provider === null);
  const [comparator, setComparator] = useState<Comparator>(
    (scenario.comparator as Comparator | null) ?? "gte",
  );
  // Numeric(18,4) 문자열("95000.0000")을 입력칸에는 사람 숫자로 보여준다
  const trim = (v: string | null) => (v === null ? "" : String(Number(v)));
  const [targetValue, setTargetValue] = useState(trim(scenario.target_value));
  const [targetLow, setTargetLow] = useState(trim(scenario.target_low));
  const [targetHigh, setTargetHigh] = useState(trim(scenario.target_high));
  const [baselineDate, setBaselineDate] = useState(scenario.baseline_date ?? "");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const complete =
    series.provider !== null &&
    (comparator === "between"
      ? targetLow !== "" && targetHigh !== ""
      : comparator === "change_pct"
        ? baselineDate !== "" && targetValue !== ""
        : targetValue !== "");

  const save = async () => {
    if (!complete || saving || series.provider === null) return;
    setSaving(true);
    setNotice(null);
    const body: ResolutionPatchBody = {
      series_provider: series.provider,
      series_code: series.code ?? "",
      series_label: series.label ?? "",
      comparator,
      target_value: comparator === "between" ? null : String(targetValue),
      target_low: comparator === "between" ? String(targetLow) : null,
      target_high: comparator === "between" ? String(targetHigh) : null,
      baseline_date: comparator === "change_pct" ? baselineDate : null,
      reason: reason.trim() || null,
    };
    try {
      await api<ScenarioOut>(`/scenarios/${scenario.id}/resolution`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setReason("");
      setNotice("조건을 담아 두었습니다. 닿는 날 알려드립니다.");
    } catch (e: unknown) {
      setNotice(apiErrorMessage(e, "조건을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card" style={{ marginBottom: 10 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <b style={{ fontSize: "var(--text-sm)" }}>
            {String(index + 1).padStart(2, "0")} {scenario.name}
          </b>
          <span className="how how--auto" style={{ marginLeft: 8 }}>
            {HOW_LABEL.auto}
          </span>
        </div>
      </div>

      <div className="row" style={{ marginTop: "var(--s3)", justifyContent: "space-between" }}>
        <div style={{ fontSize: "var(--text-sm)" }}>
          {series.label !== null ? (
            <>
              <b>{series.label}</b>
              <span className="mono" style={{ fontSize: "var(--text-micro)", color: "var(--ink-2)" }}>
                {" "}
                {series.provider} · {series.code}
              </span>
            </>
          ) : (
            <span className="empty">확인에 쓸 계열을 골라 주세요</span>
          )}
        </div>
        <button type="button" className="btn btn--sm" onClick={() => setPicking((p) => !p)}>
          {picking ? "닫기" : series.provider === null ? "계열 찾기" : "바꾸기"}
        </button>
      </div>
      {picking && (
        <div style={{ marginTop: "var(--s3)" }}>
          <SeriesSearch
            placeholder="종목·지수·거시 계열 검색"
            onPick={(entry) => {
              setSeries({ provider: entry.provider, code: entry.code, label: entry.label });
              setPicking(false);
              setNotice(null);
            }}
          />
        </div>
      )}

      <div className="cond-form">
        <select
          value={comparator}
          onChange={(e) => setComparator(e.target.value as Comparator)}
          aria-label="비교 방식"
        >
          {Object.entries(COMPARATOR_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        {comparator === "between" ? (
          <>
            <input
              type="number"
              value={targetLow}
              onChange={(e) => setTargetLow(e.target.value)}
              placeholder="하한"
              aria-label="목표 하한"
            />
            <span style={{ color: "var(--ink-2)" }}>~</span>
            <input
              type="number"
              value={targetHigh}
              onChange={(e) => setTargetHigh(e.target.value)}
              placeholder="상한"
              aria-label="목표 상한"
            />
          </>
        ) : (
          <input
            type="number"
            value={targetValue}
            onChange={(e) => setTargetValue(e.target.value)}
            placeholder={comparator === "change_pct" ? "변화율 %" : "목표값"}
            aria-label="목표값"
          />
        )}
        {comparator === "change_pct" && (
          <input
            type="date"
            value={baselineDate}
            onChange={(e) => setBaselineDate(e.target.value)}
            aria-label="기준일"
          />
        )}
        <button type="button" className="btn" onClick={save} disabled={saving || !complete}>
          {saving ? "담는 중…" : "이 조건으로 확인"}
        </button>
      </div>

      <div className="hint" style={{ marginTop: "var(--s3)" }}>
        {OBSERVATION_RULE}
      </div>
      <input
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="조건을 바꾼 이유 (선택)"
        aria-label="조건을 바꾼 이유"
        style={{
          marginTop: "var(--s3)",
          width: "100%",
          minHeight: 44,
          padding: "0 var(--s3)",
          border: "1px solid var(--line-1)",
          borderRadius: "var(--r2)",
          background: "var(--paper-0)",
          color: "var(--ink-0)",
          font: "inherit",
          fontSize: "var(--text-sm)",
        }}
      />
      {notice && (
        <p className="empty" style={{ marginTop: "var(--s2)" }}>
          {notice}
        </p>
      )}
    </div>
  );
}

// ── ③ 지켜보는 수치 — 판정하지 않는다 ──────────────────────────────────────

function WatchAdder({
  noteId,
  onAdded,
}: {
  noteId: string;
  onAdded: (watch: WatchOut) => void;
}) {
  const [notice, setNotice] = useState<string | null>(null);
  return (
    <div>
      <SeriesSearch
        placeholder="지켜볼 계열 검색해 담기"
        onPick={(entry) => {
          setNotice(null);
          api<WatchOut>(`/notes/${noteId}/watches`, {
            method: "POST",
            body: JSON.stringify({
              provider: entry.provider,
              code: entry.code,
              label: entry.label,
            }),
          })
            .then(onAdded)
            .catch((e: unknown) => {
              setNotice(apiErrorMessage(e, "담지 못했습니다. 잠시 후 다시 시도해 주세요."));
            });
        }}
      />
      {notice && (
        <p className="empty" style={{ marginTop: "var(--s2)" }}>
          {notice}
        </p>
      )}
    </div>
  );
}

// ── 화면 조립 ───────────────────────────────────────────────────────────────

export default function SetupPage() {
  const { id } = useParams<{ id: string }>();
  const [note, setNote] = useState<NoteDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [watches, setWatches] = useState<WatchOut[]>([]);

  useEffect(() => {
    api<NoteDetail>(`/notes/${id}`)
      .then((n) => {
        setNote(n);
        setWatches(n.watches);
      })
      .catch((e: unknown) => {
        setError(
          e instanceof ApiError && e.status === 404
            ? "이 노트를 찾을 수 없습니다. 목록에서 다시 열어 주세요."
            : "노트를 불러오지 못했습니다. 잠시 후 다시 열어 주세요.",
        );
      });
  }, [id]);

  const removeWatch = (watchId: string) => {
    api(`/watches/${watchId}`, { method: "DELETE" })
      .then(() => setWatches((prev) => prev.filter((w) => w.id !== watchId)))
      .catch(() => undefined);
  };

  const galae = note ? [...note.galae].sort((a, b) => a.position - b.position) : [];

  return (
    <main>
      <div className="appbar">
        <Link href={`/notes/${id}`} className="btn btn--quiet btn--sm">
          ← 노트
        </Link>
        <div>
          <h2>결과를 자동으로 확인해 드릴까요?</h2>
          <div className="sub">2단계 · 어떻게 보시는지와, 어떻게 확인할지</div>
        </div>
      </div>
      <div className="pad" style={{ maxWidth: 760 }}>
        {error && <p className="empty">{error}</p>}
        {!error && note === null && <p className="empty">불러오는 중…</p>}
        {note && (
          <>
            <p style={{ fontSize: "var(--text-sm)", color: "var(--ink-1)", maxWidth: "60ch" }}>
              지금 안 하셔도 됩니다. 나중에 노트에서 정하실 수 있습니다.
            </p>

            {galae.map((g, gi) => {
              const scenarios = [...g.scenarios].sort((a, b) => a.position - b.position);
              const autos = scenarios.filter((s) => s.resolution_type === "auto");
              return (
                <div key={g.id}>
                  <div className="sec-label" style={{ marginTop: "var(--s6)" }}>
                    갈래 {gi + 1} · {g.question}
                    {g.judge_end !== null ? ` · ${fmtDate(g.judge_end)}` : ""}
                  </div>

                  <ProbabilitySplitter galae={g} />

                  {autos.length > 0 ? (
                    autos.map((s) => (
                      <ConditionEditor key={s.id} scenario={s} index={scenarios.indexOf(s)} />
                    ))
                  ) : (
                    <div className="card" style={{ marginBottom: 10 }}>
                      <div style={{ fontSize: "var(--text-sm)" }}>
                        <b>이 질문은 수치로 확인할 수 없습니다</b>
                      </div>
                      <p
                        style={{
                          fontSize: "var(--text-caption)",
                          color: "var(--ink-1)",
                          marginTop: 4,
                          lineHeight: 1.7,
                        }}
                      >
                        판단 시점이 오면 제가 여쭙겠습니다. 대신 지켜볼 수치를 담아둘까요?
                        판정에는 쓰지 않고 추이만 보여드립니다.
                      </p>
                      <div style={{ marginTop: "var(--s3)" }}>
                        <WatchAdder
                          noteId={note.id}
                          onAdded={(w) => setWatches((prev) => [...prev, w])}
                        />
                      </div>
                    </div>
                  )}
                </div>
              );
            })}

            <div className="sec-label" style={{ marginTop: "var(--s6)" }}>
              {WATCH_SECTION_LABEL}
            </div>
            <div className="card">
              {watches.length > 0 && (
                <div className="ind" style={{ marginBottom: "var(--s3)" }}>
                  {watches.map((w) => (
                    <div className="ind__row" key={w.id}>
                      <div className="ind__n">
                        {w.label}
                        <small>
                          {w.provider} · {w.code}
                        </small>
                      </div>
                      <div className="ind__s">추이만</div>
                      <button
                        type="button"
                        className="btn btn--quiet btn--sm"
                        onClick={() => removeWatch(w.id)}
                      >
                        빼기
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <WatchAdder noteId={note.id} onAdded={(w) => setWatches((prev) => [...prev, w])} />
              <div className="hint" style={{ marginTop: "var(--s3)" }}>
                {WATCH_NOTE}
              </div>
            </div>

            <div className="row" style={{ marginTop: "var(--s5)" }}>
              {/* 블록마다 그 자리에서 담기므로, 여기서는 돌아가기만 있으면 된다 */}
              <Link href={`/notes/${id}`} className="btn btn--primary">
                노트로 돌아가기
              </Link>
            </div>
            <p className="disclaimer">{DISCLAIMER}</p>
          </>
        )}
      </div>
    </main>
  );
}
