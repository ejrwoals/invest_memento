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

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      setLoading(false);
      if (!session) return;
      setEmail(session.user.email ?? null);
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/me`, {
          headers: { Authorization: `Bearer ${session.access_token}` },
        });
        setMe(`${res.status} ${JSON.stringify(await res.json())}`);
      } catch {
        setMe("API 연결 실패 — apps/api가 떠 있는지 확인");
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
