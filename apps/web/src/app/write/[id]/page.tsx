"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ApiError,
  api,
  apiErrorDetail,
  apiErrorMessage,
  apiFetch,
  type BuildOut,
  type ConversationOut,
  type DraftGalae,
  type DraftPayload,
  type IssueOut,
  type NoteDetail,
  type NoteDraftBody,
} from "@/lib/api";
import { fmtDate } from "@/lib/format";
import {
  DISCLAIMER,
  RESIDUAL_NAME,
  STREAM_RETRY_NOTICE,
  TAG_USER,
  TARGET_TYPE_LABEL,
} from "@/lib/terms";

// 대화형 노트 작성 (ux §3.2) + 초안 확인 (같은 라우트의 상태 전환).
// 대화 이력의 정본은 서버다 — 사용자 발화는 스트리밍 전에 커밋되므로
// 스트리밍이 끊겨도 대화는 보존된다. 화면은 그 사실을 문장으로 알린다.

type Mode = "chat" | "preview" | "ask" | "saved";

interface ChatMessage {
  key: string;
  role: "user" | "assistant";
  content: string;
}

type SseEvent =
  | { type: "user_message"; id: string; seq: number }
  | { type: "delta"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; message: { id: string; seq: number; role: string; content: string } };

const SAVE_FAILED = "저장하지 못했습니다. 잠시 후 다시 시도해 주세요.";
const BUILD_FAILED = "노트 정리에 실패했습니다. 대화 내용은 그대로 있으니 다시 시도해 주세요.";

// 대화가 모으는 것 — 진행 요약 패널(측면). 대화 중에는 구조화된 판정이 없으므로
// 무엇을 모으는지만 알린다. 채워짐 표시는 초안 확인 화면의 일이다.
const GATHER_ROWS: [string, string][] = [
  ["투자 대상", "어떤 종목·자산·테마인가"],
  ["핵심 가설", "무엇이 어떻게 되리라 보는가"],
  ["반대 시나리오", "반대로 흘러간다면 어떤 모습인가"],
  ["판단 시점", "언제쯤 판가름 나는가"],
  ["근거 항목", "그 전에 무슨 일이 먼저 일어나야 하는가"],
];

export default function WriteConversationPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  // ── 대화 상태 ──
  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [streamText, setStreamText] = useState<string | null>(null);
  const [chatNotice, setChatNotice] = useState<string | null>(null);

  // ── 초안 상태 ──
  const [mode, setMode] = useState<Mode>("chat");
  const [building, setBuilding] = useState(false);
  const [payload, setPayload] = useState<DraftPayload | null>(null);
  const [issues, setIssues] = useState<IssueOut[]>([]);
  const [note, setNote] = useState<NoteDraftBody | null>(null); // 편집본(날짜·종류 고치기)
  const [askIssues, setAskIssues] = useState<IssueOut[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [symbolFallback, setSymbolFallback] = useState(false);
  const [savedNoteId, setSavedNoteId] = useState<string | null>(null);

  const chatRef = useRef<HTMLDivElement>(null);
  const hasUserTurn = messages.some((m) => m.role === "user");

  useEffect(() => {
    api<ConversationOut>(`/conversations/${id}`)
      .then((conv) => {
        if (conv.note_id) {
          // 이미 노트로 저장된 대화 — 노트로 보낸다
          router.replace(`/notes/${conv.note_id}`);
          return;
        }
        setMessages(
          conv.messages.map((m) => ({
            key: m.id,
            role: m.role === "user" ? "user" : "assistant",
            content: m.content,
          })),
        );
        setReady(true);
      })
      .catch((e: unknown) => {
        setLoadError(
          e instanceof ApiError && e.status === 404
            ? "이 대화를 찾을 수 없습니다. 홈에서 다시 시작해 주세요."
            : "대화를 불러오지 못했습니다. 잠시 후 다시 열어 주세요.",
        );
      });
  }, [id, router]);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight });
  }, [messages, streamText]);

  // ── 발화 전송 — SSE(fetch + ReadableStream) 파싱 ──
  const send = async (e: FormEvent) => {
    e.preventDefault();
    const content = input.trim();
    if (!content || sending) return;
    setInput("");
    setSending(true);
    setChatNotice(null);
    setMessages((prev) => [...prev, { key: `local-${Date.now()}`, role: "user", content }]);
    setStreamText("");
    try {
      const res = await apiFetch(`/conversations/${id}/messages`, {
        method: "POST",
        body: JSON.stringify({ content }),
      });
      if (!res.ok || res.body === null) {
        const text = await res.text().catch(() => "");
        const detail = apiErrorDetail(new ApiError(res.status, text));
        setStreamText(null);
        setChatNotice(detail?.message ?? STREAM_RETRY_NOTICE);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let acc = "";
      let finished = false;
      const handle = (evt: SseEvent) => {
        if (evt.type === "delta") {
          acc += evt.text;
          setStreamText(acc);
        } else if (evt.type === "error") {
          setStreamText(null);
          setChatNotice(`${evt.message} 말씀하신 내용은 저장되어 있습니다.`);
          finished = true;
        } else if (evt.type === "done") {
          setStreamText(null);
          setMessages((prev) => [
            ...prev,
            { key: evt.message.id, role: "assistant", content: evt.message.content },
          ]);
          finished = true;
        }
      };
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let cut: number;
        while ((cut = buf.indexOf("\n\n")) !== -1) {
          const frame = buf.slice(0, cut);
          buf = buf.slice(cut + 2);
          for (const line of frame.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            try {
              handle(JSON.parse(line.slice(6)) as SseEvent);
            } catch {
              // 조각난 프레임은 버린다 — 다음 done 이 정본이다
            }
          }
        }
      }
      if (!finished) {
        // done 없이 끊김 — 서버에 저장되지 않은 부분 응답은 화면에 남기지 않는다
        setStreamText(null);
        setChatNotice(STREAM_RETRY_NOTICE);
      }
    } catch {
      setStreamText(null);
      setChatNotice(STREAM_RETRY_NOTICE);
    } finally {
      setSending(false);
    }
  };

  // ── 조립 (여기까지 정리하기 · 다시 만들기) ──
  const runBuild = async () => {
    if (building || sending) return;
    setBuilding(true);
    setChatNotice(null);
    setSaveError(null);
    setSymbolFallback(false);
    try {
      const out = await api<BuildOut>(`/conversations/${id}/build`, { method: "POST" });
      setPayload(out.draft_note);
      setIssues(out.issues);
      setNote(structuredClone(out.draft_note.note));
      setMode("preview");
    } catch (e: unknown) {
      const message = apiErrorMessage(e, BUILD_FAILED);
      if (mode === "chat") setChatNotice(message);
      else setSaveError(message);
    } finally {
      setBuilding(false);
    }
  };

  // ── 초안 편집 — AI가 해석한 날짜 고치기, 대상 종류 고르기 ──
  const setJudgeEnd = (gi: number, value: string) => {
    setNote((prev) => {
      if (!prev) return prev;
      const galae = prev.galae.map((g, i) =>
        i === gi
          ? { ...g, judge_end: value || null, judge_kind: g.judge_kind ?? ("date" as const) }
          : g,
      );
      return { ...prev, galae };
    });
  };

  const setTargetType = (value: string) => {
    setNote((prev) => {
      if (!prev) return prev;
      const t = value === "ticker" || value === "asset" || value === "theme" ? value : null;
      return { ...prev, target_type: t };
    });
  };

  // ── 저장 — 검증 재실행 → ask 는 저장당 1회 되묻기 → POST /notes ──
  const doSave = async (n: NoteDraftBody) => {
    const body = {
      ...n,
      conversation_id: id,
      quote:
        payload?.quote !== null && payload?.quote !== undefined
          ? { text: payload.quote.text, quoted_from: payload.quote.quoted_from, derived: false }
          : null,
    };
    try {
      const saved = await api<NoteDetail>("/notes", { method: "POST", body: JSON.stringify(body) });
      // 저장 직후 2단계로 이어지는 갈림길 — `나중에 하기`가 같은 크기의 1급 선택지다 (P6)
      setSavedNoteId(saved.id);
      setMode("saved");
    } catch (e: unknown) {
      const detail = apiErrorDetail(e);
      if (detail?.code === "UNKNOWN_SYMBOL") {
        setSymbolFallback(true);
        setSaveError(
          `종목 코드 '${n.target_symbol ?? ""}'를 확인하지 못했습니다. 코드 없이 저장할 수 있습니다.`,
        );
      } else {
        setSaveError(apiErrorMessage(e, SAVE_FAILED));
      }
      setMode("preview");
    }
  };

  const trySave = async () => {
    if (!note || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const fresh = await api<IssueOut[]>("/notes/validate", {
        method: "POST",
        body: JSON.stringify(note),
      });
      setIssues(fresh);
      if (fresh.some((i) => i.severity === "blocking")) {
        setMode("preview");
        return;
      }
      const asks = fresh.filter((i) => i.severity === "ask");
      if (asks.length > 0 && mode !== "ask") {
        // 되묻기는 저장 시도당 한 화면 — 개별 팝업으로 쪼개지 않는다 (development-plan §3.1)
        setAskIssues(asks);
        setMode("ask");
        return;
      }
      await doSave(note);
    } catch (e: unknown) {
      setSaveError(apiErrorMessage(e, SAVE_FAILED));
      setMode("preview");
    } finally {
      setSaving(false);
    }
  };

  const saveWithoutSymbol = () => {
    if (!note || saving) return;
    const n = { ...note, target_symbol: null };
    setNote(n);
    setSymbolFallback(false);
    setSaveError(null);
    setSaving(true);
    void doSave(n).finally(() => setSaving(false));
  };

  // ── 화면 ──

  if (loadError) {
    return (
      <main>
        <div className="appbar">
          <Link href="/" className="btn btn--quiet btn--sm">
            ← 홈
          </Link>
          <h2>새 노트</h2>
        </div>
        <div className="pad">
          <p className="empty">{loadError}</p>
        </div>
      </main>
    );
  }

  if (mode === "saved" && savedNoteId !== null) {
    return (
      <main>
        <div className="appbar">
          <div>
            <h2>노트가 저장되었습니다</h2>
            <div className="sub">이어서 확인 방법을 정하시겠어요?</div>
          </div>
        </div>
        <div className="pad" style={{ maxWidth: 640 }}>
          <p style={{ fontSize: "var(--text-sm)", color: "var(--ink-1)", maxWidth: "60ch" }}>
            확률을 나누고, 수치로 확인할 수 있는 답에는 조건을 걸어 둘 수 있습니다.
            지금 안 하셔도 됩니다 — 나중에 노트에서 정하실 수 있습니다.
          </p>
          <div className="row" style={{ marginTop: "var(--s5)" }}>
            {/* `나중에 하기`는 같은 크기 — 흐리게 처리하지 않는다 (ux §3.3) */}
            <Link href={`/notes/${savedNoteId}/setup`} className="btn btn--primary">
              확인 방법 정하기
            </Link>
            <Link href={`/notes/${savedNoteId}`} className="btn">
              나중에 하기
            </Link>
          </div>
          <p className="disclaimer">{DISCLAIMER}</p>
        </div>
      </main>
    );
  }

  if (mode === "ask") {
    return (
      <main>
        <div className="appbar">
          <button className="btn btn--quiet btn--sm" onClick={() => setMode("preview")}>
            ← 초안
          </button>
          <div>
            <h2>비워 둔 곳이 있습니다</h2>
            <div className="sub">비워도 저장됩니다 — 확인만 부탁드립니다</div>
          </div>
        </div>
        <div className="pad" style={{ maxWidth: 640 }}>
          {askIssues.map((i) => (
            <p key={i.code} className="hint" style={{ marginBottom: "var(--s3)" }}>
              {i.message}
            </p>
          ))}
          <div className="row" style={{ marginTop: "var(--s5)" }}>
            {/* 비운 채로 저장은 같은 크기의 선택지 — 작게 숨기면 사실상 차단이다 */}
            <button className="btn" onClick={trySave} disabled={saving}>
              {saving ? "저장하는 중…" : "비운 채로 저장"}
            </button>
            <button className="btn" onClick={() => setMode("preview")} disabled={saving}>
              돌아가서 채우기
            </button>
            <button className="btn btn--quiet" onClick={() => setMode("chat")} disabled={saving}>
              대화로 돌아가기
            </button>
          </div>
          {saveError && (
            <p className="empty" style={{ marginTop: "var(--s4)" }}>
              {saveError}
            </p>
          )}
        </div>
      </main>
    );
  }

  if (mode === "preview" && note && payload) {
    const derivedByIndex = new Map(payload.derived_judges.map((d) => [d.galae_index, d]));
    const blocking = issues.filter((i) => i.severity === "blocking");
    const notices = issues.filter((i) => i.severity === "notice" || i.severity === "incomplete");
    const needsType = note.target_name.trim() !== "" && note.target_type === null;
    const premiseIssue = issues.find((i) => i.code === "NO_PREMISE");
    const anyQuotedPremise = note.premises.some((p) => p.quoted_from !== null);

    const renderGalae = (g: DraftGalae, gi: number) => {
      const derived = derivedByIndex.get(gi);
      const untouched = derived !== undefined && g.judge_end === derived.judge_end;
      return (
        <div className="slot" key={gi}>
          <div className="slot__k">갈래 {gi + 1}</div>
          <div className="slot__v">
            <div>
              <em>{g.question.trim() || note.thesis_summary}</em>
            </div>
            {!g.question.trim() && (
              <div className="empty">
                무엇을 놓고 갈리는지 대화에 나오지 않아 가설 문장을 임시로 보여드립니다
              </div>
            )}
            <div className="derived">
              {untouched ? (
                <>
                  “{derived.source_text ?? "말씀하신 표현"}”을 <b>{fmtDate(g.judge_end!)}</b>로
                  읽었습니다
                </>
              ) : g.judge_end !== null ? (
                <>
                  판단 시점 <b>{fmtDate(g.judge_end)}</b>
                </>
              ) : (
                <>판단 시점이 비어 있습니다 · 정하면 리마인드가 시작됩니다</>
              )}
              <input
                type="date"
                value={g.judge_end ?? ""}
                onChange={(e) => setJudgeEnd(gi, e.target.value)}
                aria-label={`갈래 ${gi + 1} 판단 시점 고치기`}
              />
            </div>
            <div style={{ marginTop: 9 }}>
              {g.scenarios.map((s, si) => (
                <div key={si} style={si > 0 ? { marginTop: 4 } : undefined}>
                  <em>{String(si + 1).padStart(2, "0")}</em> {s.name}
                  {s.description && <span className="empty"> — {s.description}</span>}
                </div>
              ))}
              {g.scenarios.length === 0 && (
                <div className="empty">
                  대화에서 답의 모습이 나오지 않아 시나리오를 비워 두었습니다
                </div>
              )}
              <div style={{ marginTop: 4, color: "var(--ink-2)" }}>
                <em>{String(g.scenarios.length + 1).padStart(2, "0")}</em> {RESIDUAL_NAME}{" "}
                <span className="empty">— 저장하면 함께 붙습니다</span>
              </div>
            </div>
          </div>
        </div>
      );
    };

    return (
      <main>
        <div className="appbar">
          <button className="btn btn--quiet btn--sm" onClick={() => setMode("chat")}>
            ← 대화
          </button>
          <div>
            <h2>이렇게 정리했습니다</h2>
            <div className="sub">고칠 곳이 있으면 지금 바꾸세요</div>
          </div>
        </div>
        <div className="pad">
          <div style={{ maxWidth: 760 }}>
            <div className="slot">
              <div className="slot__k">대상</div>
              <div className="slot__v">
                {note.target_name.trim() ? (
                  <>
                    <em>{note.target_name}</em>
                    {note.target_symbol && <span className="mono"> {note.target_symbol}</span>}
                    {needsType && (
                      <div className="derived">
                        어떤 종류인지 대화에서 정해지지 않았습니다
                        <select
                          value=""
                          onChange={(e) => setTargetType(e.target.value)}
                          aria-label="대상 종류 고르기"
                        >
                          <option value="" disabled>
                            종류 고르기
                          </option>
                          {Object.entries(TARGET_TYPE_LABEL).map(([k, v]) => (
                            <option key={k} value={k}>
                              {v}
                            </option>
                          ))}
                        </select>
                      </div>
                    )}
                    {note.target_type !== null && (
                      <span className="empty"> · {TARGET_TYPE_LABEL[note.target_type]}</span>
                    )}
                  </>
                ) : (
                  <span className="empty">대화에서 투자 대상이 정해지지 않아 비어 있습니다</span>
                )}
              </div>
            </div>

            <div className="slot">
              <div className="slot__k">가설</div>
              <div className="slot__v">
                {note.thesis_summary.trim() ? (
                  <>
                    <em>{note.thesis_summary}</em>
                    {note.thesis_detail && (
                      <div style={{ marginTop: 6, color: "var(--ink-1)" }}>
                        {note.thesis_detail}
                      </div>
                    )}
                    {payload.quote && (
                      <div className="quote" style={{ marginTop: 9 }}>
                        {payload.quote.authorship === "user" && (
                          <span className="tag-user">{TAG_USER}</span>
                        )}
                        “{payload.quote.text}”
                      </div>
                    )}
                  </>
                ) : (
                  <span className="empty">대화에서 가설 문장이 나오지 않아 비어 있습니다</span>
                )}
              </div>
            </div>

            <div className="slot">
              <div className="slot__k">근거 항목</div>
              <div className="slot__v">
                {note.premises.length > 0 ? (
                  <>
                    <ol className="premise" style={{ marginTop: -9 }}>
                      {note.premises.map((p, i) => (
                        <li key={i}>{p.statement}</li>
                      ))}
                    </ol>
                    {anyQuotedPremise && (
                      <div className="empty" style={{ marginTop: 6 }}>
                        <span className="tag-user">{TAG_USER}</span> 말씀하신 그대로 옮겼습니다.
                        다듬지 않습니다.
                      </div>
                    )}
                  </>
                ) : (
                  <span className="empty">
                    {premiseIssue?.message ??
                      "대화에서 근거로 남길 문장이 나오지 않아 비워 두었습니다"}
                  </span>
                )}
              </div>
            </div>

            {note.galae.length > 0 ? (
              note.galae.map(renderGalae)
            ) : (
              <div className="slot">
                <div className="slot__k">갈래</div>
                <div className="slot__v">
                  <span className="empty">
                    판가름할 질문이 대화에 나오지 않아 비워 두었습니다. 대화로 돌아가
                    이야기하시면 채워집니다.
                  </span>
                </div>
              </div>
            )}

            <div className="slot">
              <div className="slot__k">확률</div>
              <div className="slot__v">
                <span className="empty">
                  아직 비어 있습니다 · 저장한 뒤 노트에서 갈래마다 나눕니다
                </span>
              </div>
            </div>
          </div>

          {blocking.map((i) => (
            <p key={i.code} className="hint" style={{ marginTop: "var(--s4)", maxWidth: 760 }}>
              {i.message}
            </p>
          ))}
          {notices.map((i) => (
            <p key={i.code} className="empty" style={{ marginTop: "var(--s3)", maxWidth: 760 }}>
              {i.message}
            </p>
          ))}
          {needsType && (
            <p className="empty" style={{ marginTop: "var(--s3)" }}>
              대상의 종류를 고르면 저장할 수 있습니다.
            </p>
          )}

          <div className="row" style={{ marginTop: "var(--s5)" }}>
            <button
              className="btn btn--primary"
              onClick={trySave}
              disabled={saving || building || blocking.length > 0 || needsType}
            >
              {saving ? "저장하는 중…" : "저장하기"}
            </button>
            <button className="btn" onClick={runBuild} disabled={building || saving}>
              {building ? "다시 정리하는 중…" : "다시 만들기"}
            </button>
            <button
              className="btn btn--quiet"
              onClick={() => setMode("chat")}
              disabled={building || saving}
            >
              대화로 돌아가기
            </button>
          </div>
          {saveError && (
            <p className="empty" style={{ marginTop: "var(--s4)" }}>
              {saveError}
            </p>
          )}
          {symbolFallback && (
            <button
              className="btn btn--sm"
              style={{ marginTop: "var(--s3)" }}
              onClick={saveWithoutSymbol}
              disabled={saving}
            >
              코드 없이 저장하기
            </button>
          )}
          <p className="disclaimer">{DISCLAIMER}</p>
        </div>
      </main>
    );
  }

  // ── 대화 화면 ──
  return (
    <main className="wshell">
      <div className="appbar">
        <Link href="/" className="btn btn--quiet btn--sm">
          ← 홈
        </Link>
        <div>
          <h2>새 노트</h2>
          <div className="sub">생각을 말하면 AI가 노트로 정리합니다</div>
        </div>
        <span className="spacer" />
        <button
          className="btn btn--primary"
          onClick={runBuild}
          disabled={!hasUserTurn || sending || building || !ready}
        >
          {building ? "정리하는 중…" : "여기까지 정리하기"}
        </button>
      </div>
      <div className="wsplit">
        <div className="chatcol">
          <div className="chat" ref={chatRef}>
            {!ready && <p className="empty">대화를 불러오는 중…</p>}
            {messages.map((m) =>
              m.role === "assistant" ? (
                <div key={m.key} className="msg msg--ai">
                  <b>AI</b>
                  <div>{m.content}</div>
                </div>
              ) : (
                <div key={m.key} className="msg msg--me">
                  {m.content}
                </div>
              ),
            )}
            {streamText !== null && (
              <div className="msg msg--ai">
                <b>AI</b>
                <div>{streamText || "…"}</div>
              </div>
            )}
            {chatNotice && <div className="hint">{chatNotice}</div>}
          </div>
          <form className="composer" onSubmit={send}>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="계속 이야기하기"
              aria-label="메시지 입력"
              disabled={sending || !ready}
            />
            <button className="btn btn--primary" disabled={sending || !ready || !input.trim()}>
              {sending ? "받는 중…" : "보내기"}
            </button>
          </form>
        </div>
        <aside className="build" aria-label="이 대화가 모으는 것">
          <div className="sec-label">이 대화가 모으는 것</div>
          {GATHER_ROWS.map(([k, v]) => (
            <div className="build__row" key={k}>
              <i>·</i>
              <div>
                <b>{k}</b> · {v}
              </div>
            </div>
          ))}
          <div className="hint" style={{ marginTop: "var(--s4)" }}>
            비어 있어도 저장됩니다. 판단 시점만 있으면 리마인드는 옵니다.
          </div>
        </aside>
      </div>
    </main>
  );
}
