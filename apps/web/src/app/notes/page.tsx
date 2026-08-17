"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type NoteSummary } from "@/lib/api";
import { isDueSoon, judgeDueText } from "@/lib/format";
import { EMPTY_NOTES, INCOMPLETE_NOTICE } from "@/lib/terms";

// 노트 목록 (ux §3.6) — 정렬 기본값은 다음 판단 시점이 가까운 순 (P1).
// 판단 시점이 없는 노트는 아래로 내려간다.
function sortByDue(notes: NoteSummary[]): NoteSummary[] {
  return [...notes].sort((a, b) => {
    if (a.next_judge_end === null && b.next_judge_end === null) return 0;
    if (a.next_judge_end === null) return 1;
    if (b.next_judge_end === null) return -1;
    return a.next_judge_end.localeCompare(b.next_judge_end);
  });
}

export default function NotesPage() {
  const [notes, setNotes] = useState<NoteSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<NoteSummary[]>("/notes")
      .then((data) => setNotes(sortByDue(data)))
      .catch(() => setError("노트를 불러오지 못했습니다. 잠시 후 다시 열어 주세요."));
  }, []);

  return (
    <main>
      <div className="appbar">
        <Link href="/" className="btn btn--quiet btn--sm">
          ← 홈
        </Link>
        <h2>노트</h2>
        <span className="spacer" />
        <Link href="/write" className="btn btn--sm">
          + 새 노트
        </Link>
      </div>
      <div className="pad">
        {error && <p className="empty">{error}</p>}
        {!error && notes === null && <p className="empty">불러오는 중…</p>}
        {notes !== null && notes.length === 0 && <p className="empty">{EMPTY_NOTES}</p>}
        {notes !== null && notes.length > 0 && (
          <div className="nlist">
            {notes.map((n) => (
              <Link key={n.id} href={`/notes/${n.id}`} className="nrow">
                <div className="nrow__h">
                  <span className="nrow__dot" style={{ background: n.color }} aria-hidden />
                  <span className="nrow__t">{n.target_name}</span>
                  {n.next_judge_end !== null && (
                    <span
                      className={
                        isDueSoon(n.next_judge_end) ? "nrow__due nrow__due--soon" : "nrow__due"
                      }
                    >
                      {judgeDueText(n.next_judge_end)}
                    </span>
                  )}
                </div>
                <div className="nrow__s">{n.thesis_summary}</div>
                <div className="nrow__m">
                  갈래 {n.galae_count}
                  {!n.is_complete && <> · {INCOMPLETE_NOTICE}</>}
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
