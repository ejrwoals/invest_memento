"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ApiError, api, type GalaeOut, type NoteDetail, type ScenarioOut } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import {
  DISCLAIMER,
  GALAE_HEADING_SUFFIX,
  HOW_CLASS,
  HOW_LABEL,
  HOW_NOTE,
  INCOMPLETE_NOTICE,
  RESIDUAL_NOTE,
} from "@/lib/terms";

// 노트 상세 (ux §3.4) — 나의 사고가 본문. 가설 → 성립 조건 → 갈래 블록.
// 확률 도넛·새 정보 서랍·추이 차트는 다음 단계.

function ScenarioCard({ s, index }: { s: ScenarioOut; index: number }) {
  const note = s.is_residual ? RESIDUAL_NOTE : HOW_NOTE[s.resolution_type];
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
      {note && (
        <div className="branch__meta">
          <span>{note}</span>
        </div>
      )}
    </article>
  );
}

function GalaeBlock({ g, index }: { g: GalaeOut; index: number }) {
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
            <ScenarioCard key={s.id} s={s} index={i} />
          ))}
        </div>
      </div>
    </>
  );
}

export default function NoteDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [note, setNote] = useState<NoteDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  const galae = note ? [...note.galae].sort((a, b) => a.position - b.position) : [];
  const premises = note ? [...note.premises].sort((a, b) => a.position - b.position) : [];

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
              <GalaeBlock key={g.id} g={g} index={i} />
            ))}

            <p className="disclaimer">{DISCLAIMER}</p>
          </>
        )}
      </div>
    </main>
  );
}
