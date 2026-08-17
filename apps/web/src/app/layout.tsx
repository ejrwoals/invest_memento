import type { Metadata } from "next";
import "./globals.css";

// 폰트는 토큰(--font-sans)의 시스템 폴백으로 시작한다 — 외부 요청 없음.
// Pretendard 셀프호스팅은 다음 단계.

export const metadata: Metadata = {
  title: "Investment Memento",
  description: "투자 판단을 기록하고 되짚는 메멘토",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
