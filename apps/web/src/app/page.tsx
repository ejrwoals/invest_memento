"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { createClient } from "@/lib/supabase/client";

// M1 검증용 임시 홈: 로그인 상태 + FastAPI /me 응답 확인.
// 실제 홈 화면(타임라인+피드)은 M2 이후 이 자리를 대체한다.
export default function Home() {
  const [email, setEmail] = useState<string | null>(null);
  const [me, setMe] = useState<string>("(대기)");
  const [loading, setLoading] = useState(true);
  const [draftId, setDraftId] = useState<string | null>(null);

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      setLoading(false);
      if (!session) return;
      setEmail(session.user.email ?? null);
      const headers = { Authorization: `Bearer ${session.access_token}` };
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/me`, { headers });
        setMe(`${res.status} ${JSON.stringify(await res.json())}`);
      } catch {
        setMe("API 연결 실패 — apps/api가 떠 있는지 확인");
      }
      // 작성하다 만 대화 — 있으면 조용히 재개 링크만 띄운다 (ux §3.2 이탈과 재개)
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/conversations?status=draft`, {
          headers,
        });
        if (res.ok) {
          const drafts = (await res.json()) as { id: string }[];
          if (drafts.length > 0) setDraftId(drafts[0].id);
        }
      } catch {
        // 홈은 재개 링크 없이도 성립한다 — 실패를 알리지 않는다
      }
    });
  }, []);

  const signOut = async () => {
    await createClient().auth.signOut();
    location.reload();
  };

  if (loading) return null;

  return (
    <main style={{ maxWidth: 640, margin: "80px auto", fontFamily: "sans-serif" }}>
      <h1>Investment Memento</h1>
      {email ? (
        <>
          <p>로그인: {email}</p>
          <p>
            FastAPI <code>/me</code>: <code>{me}</code>
          </p>
          <p>
            <Link href="/notes" style={{ textDecoration: "underline" }}>
              노트 목록 →
            </Link>
          </p>
          <p>
            <Link href="/write" style={{ textDecoration: "underline" }}>
              + 새 노트
            </Link>
          </p>
          {draftId && (
            <p>
              <Link href={`/write?resume=${draftId}`} style={{ textDecoration: "underline" }}>
                작성하던 노트 이어가기 →
              </Link>
            </p>
          )}
          <button onClick={signOut}>로그아웃</button>
        </>
      ) : (
        <p>
          <a href="/login">로그인하러 가기</a>
        </p>
      )}
    </main>
  );
}
