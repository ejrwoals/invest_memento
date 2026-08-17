"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { createClient } from "@/lib/supabase/client";
import { api, type FeedCardOut, type HomeOut, type TimelineEntryOut } from "@/lib/api";
import { daysLeftText, daysUntil, fmtShortDate } from "@/lib/format";
import {
  EMPTY_FEED,
  EMPTY_FEED_PROMPT,
  EMPTY_TIMELINE,
  FEED_WHY,
} from "@/lib/terms";

// 홈 (ux §3.1) — 위는 지금 해야 할 일(리마인드 피드, 우선순위 순), 아래는 미래 조망
// (세로 타임라인, 시간 순). 카드에 닫기(X)는 없다 — 무시가 기록되면 부채감이 된다 (P5).
// 우선순위·정렬은 서버(home.py)가 끝냈다 — 클라이언트는 자르고 묶기만 한다.

const FEED_LIMIT = 3; // 기본 3장, 나머지는 더 보기 (알림 피로 방지)
const PERIOD_FOLD = 5; // 한 구간 5개 초과는 접는다 (ux §3.1 밀집 처리)
const NEAR_DAYS = 7;

/** 타임라인 구간 — 가까운 것이 먼저 나오는 것으로 충분하다. 간격 조절 없음 */
const PERIODS: { label: string; max: number }[] = [
  { label: "이번 주", max: 7 },
  { label: "~1개월", max: 31 },
  { label: "~3개월", max: 92 },
  { label: "~6개월", max: 183 },
  { label: "그 이후", max: Number.POSITIVE_INFINITY },
];

/** 피드 카드 기호 — deadline 은 남은 날수를 그대로 쓴다 */
const FEED_ICON: Record<string, string> = {
  pending_judgment: "!",
  auto_condition_met: "◆",
  interval: "↺",
};

/** 오늘 날짜 — 기기 시간대 기준 (toISOString 은 UTC 라 하루 어긋날 수 있다) */
function todayText(): string {
  const now = new Date();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}.${mm}.${dd}`;
}

function FeedCard({ c }: { c: FeedCardOut }) {
  const days = c.date !== null ? daysUntil(c.date) : null;
  const alert =
    c.kind === "pending_judgment" ||
    c.kind === "auto_condition_met" ||
    (c.kind === "deadline" && days !== null && days <= NEAR_DAYS);
  const ico =
    c.kind === "deadline" && days !== null && days >= 0 ? `${days}일` : (FEED_ICON[c.kind] ?? "·");
  return (
    <Link href={`/notes/${c.note_id}`} className={alert ? "fcard fcard--alert" : "fcard"}>
      <div className="fcard__ico" aria-hidden>
        {ico}
      </div>
      <div className="fcard__body">
        <div className="fcard__why">{FEED_WHY[c.kind] ?? ""}</div>
        <div className="fcard__t">
          <b>{c.title}</b> · {c.reason}
        </div>
      </div>
      <div className="fcard__act">
        <span className="btn btn--sm">열어보기</span>
      </div>
    </Link>
  );
}

function TimelinePeriod({
  label,
  items,
  far,
}: {
  label: string;
  items: TimelineEntryOut[];
  far: boolean;
}) {
  const [open, setOpen] = useState(false);
  const shown = open ? items : items.slice(0, PERIOD_FOLD);
  return (
    <section className="tperiod">
      <h4 className="tperiod__h">
        <i aria-hidden />
        <span>{label}</span>
        <i aria-hidden />
      </h4>
      {shown.map((t) => {
        const near = daysUntil(t.judge_end) <= NEAR_DAYS;
        return (
          <div
            key={t.galae_id}
            className={near ? "titem titem--near" : far ? "titem titem--far" : "titem"}
          >
            <div className="titem__when">
              <b>{fmtShortDate(t.judge_end)}</b>
              {daysLeftText(t.judge_end)}
            </div>
            <span className="titem__dot" aria-hidden />
            <Link href={`/notes/${t.note_id}`} className="titem__card">
              <div className="titem__mobwhen">
                {fmtShortDate(t.judge_end)} · {daysLeftText(t.judge_end)}
              </div>
              <div className="titem__t">
                <span
                  aria-hidden
                  style={{
                    display: "inline-block",
                    width: 9,
                    height: 9,
                    borderRadius: "50%",
                    background: t.color,
                    marginRight: 7,
                    verticalAlign: -1,
                  }}
                />
                {t.note_title}
              </div>
              <div className="titem__s">{t.question}</div>
            </Link>
          </div>
        );
      })}
      {items.length > PERIOD_FOLD && !open && (
        <div className="tmore">
          <span />
          <button onClick={() => setOpen(true)}>+ {items.length - PERIOD_FOLD}개 더 보기</button>
        </div>
      )}
    </section>
  );
}

export default function Home() {
  const [loading, setLoading] = useState(true);
  const [authed, setAuthed] = useState(false);
  const [home, setHome] = useState<HomeOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [feedOpen, setFeedOpen] = useState(false);

  useEffect(() => {
    createClient()
      .auth.getSession()
      .then(({ data: { session } }) => {
        setLoading(false);
        if (!session) return;
        setAuthed(true);
        api<HomeOut>("/home")
          .then(setHome)
          .catch(() => setError("홈을 불러오지 못했습니다. 잠시 후 다시 열어 주세요."));
      });
  }, []);

  const signOut = async () => {
    await createClient().auth.signOut();
    location.reload();
  };

  if (loading) return null;

  if (!authed) {
    return (
      <main>
        <div className="appbar">
          <h2>Investment Memento</h2>
        </div>
        <div className="pad">
          <p style={{ marginBottom: "var(--s4)" }}>
            투자 판단을 기록하고, 때가 되면 되짚어 드립니다.
          </p>
          <Link href="/login" className="btn btn--primary">
            로그인하러 가기
          </Link>
        </div>
      </main>
    );
  }

  const feed = home?.feed ?? [];
  const shownFeed = feedOpen ? feed : feed.slice(0, FEED_LIMIT);
  const timeline = home?.timeline ?? [];
  const buckets = PERIODS.map((p) => ({ ...p, items: [] as TimelineEntryOut[] }));
  for (const t of timeline) {
    const days = daysUntil(t.judge_end);
    const bucket = buckets.find((b) => days <= b.max) ?? buckets[buckets.length - 1];
    bucket.items.push(t); // 서버가 judge_end 오름차순으로 준다 — 구간 안 순서 유지
  }

  return (
    <main>
      <div className="appbar">
        <h2>Investment Memento</h2>
        <span className="spacer" />
        <Link href="/notes" className="btn btn--quiet btn--sm">
          노트
        </Link>
        <Link href="/write" className="btn btn--sm">
          + 새 노트
        </Link>
        <button onClick={signOut} className="btn btn--quiet btn--sm">
          로그아웃
        </button>
      </div>
      <div className="pad">
        {error && <p className="empty">{error}</p>}
        {!error && home === null && <p className="empty">불러오는 중…</p>}
        {home && (
          <>
            {home.draft_conversation_id !== null && (
              // 작성하다 만 대화 — 조용히 재개 링크만 (ux §3.2 이탈과 재개)
              <p style={{ marginBottom: "var(--s5)", fontSize: "var(--text-sm)" }}>
                <Link
                  href={`/write?resume=${home.draft_conversation_id}`}
                  style={{ textDecoration: "underline", color: "var(--ink-1)" }}
                >
                  작성하던 노트 이어가기 →
                </Link>
              </p>
            )}

            <div className="sec-label">다시 볼 시점</div>
            {feed.length === 0 ? (
              <div style={{ fontSize: "var(--text-sm)", color: "var(--ink-1)" }}>
                <p>{EMPTY_FEED}</p>
                <p
                  className="row"
                  style={{ marginTop: "var(--s3)", color: "var(--ink-2)" }}
                >
                  {EMPTY_FEED_PROMPT}
                  <Link href="/write" className="btn btn--sm">
                    새 노트
                  </Link>
                </p>
              </div>
            ) : (
              <div className="feed">
                {shownFeed.map((c) => (
                  <FeedCard key={c.note_id} c={c} />
                ))}
                {!feedOpen && feed.length > FEED_LIMIT && (
                  <button
                    className="btn btn--quiet"
                    style={{ alignSelf: "center" }}
                    onClick={() => setFeedOpen(true)}
                  >
                    더 보기
                  </button>
                )}
              </div>
            )}

            <div className="sec-label" style={{ marginTop: "var(--s7)" }}>
              다가오는 판단 시점
            </div>
            {timeline.length === 0 ? (
              <p className="empty">{EMPTY_TIMELINE}</p>
            ) : (
              <div className="tline">
                <div className="tnow">
                  <span className="tnow__mark" aria-hidden />
                  <span className="tnow__label">오늘 · {todayText()}</span>
                </div>
                {buckets
                  .filter((b) => b.items.length > 0)
                  .map((b) => (
                    <TimelinePeriod
                      key={b.label}
                      label={b.label}
                      items={b.items}
                      far={b.label === "그 이후"}
                    />
                  ))}
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}
