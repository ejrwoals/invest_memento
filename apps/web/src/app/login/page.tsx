"use client";

import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const signIn = async () => {
    const supabase = createClient();
    await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${location.origin}/auth/callback` },
    });
  };

  return (
    <main style={{ display: "grid", placeItems: "center", minHeight: "100dvh" }}>
      <button onClick={signIn} style={{ padding: "12px 24px", fontSize: 16 }}>
        Google로 계속하기
      </button>
    </main>
  );
}
