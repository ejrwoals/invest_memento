"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, type ConversationOut } from "@/lib/api";

// 새 노트 작성 진입점 (ux §3.2) — 대화를 만들거나(?resume 이면 재개) 대화 화면으로 보낸다.
// 대화 id 가 URL 에 남아야 이탈 후에도 그대로 돌아올 수 있다.

function WriteEntry() {
  const router = useRouter();
  const params = useSearchParams();
  const resume = params.get("resume");
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // StrictMode 이중 실행 가드 — 대화가 둘 생기면 안 된다
    started.current = true;
    if (resume) {
      router.replace(`/write/${resume}`);
      return;
    }
    api<ConversationOut>("/conversations", { method: "POST", body: JSON.stringify({}) })
      .then((conv) => router.replace(`/write/${conv.id}`))
      .catch(() => setError("대화를 시작하지 못했습니다. 잠시 후 다시 시도해 주세요."));
  }, [resume, router]);

  return (
    <main className="pad">
      <p className="empty">{error ?? "대화를 준비하는 중…"}</p>
    </main>
  );
}

export default function WritePage() {
  return (
    <Suspense fallback={null}>
      <WriteEntry />
    </Suspense>
  );
}
