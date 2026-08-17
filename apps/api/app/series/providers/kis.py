"""KIS (한국투자증권) — 앱키 미준비로 인터페이스만 있다 (05 §2.4).

앱키·앱시크릿이 준비되면 구현한다: 공유 토큰(kis_tokens 한 행, select ... for update
skip locked 갱신), 인프로세스 토큰버킷 리미터, 국내·해외 일봉 REST.
그때까지 개발 환경은 series_dev_provider='yfinance' 플래그로 yfinance 어댑터를
이 자리에 끼운다 — 스키마·배치·평가 코드는 건드릴 것이 없어야 한다 (§2.5 합격 기준).
"""

from datetime import date

from app.series.providers import DailyBar


class KisProvider:
    name = "kis"

    def fetch_daily(self, code: str, start: date, end: date) -> list[DailyBar]:
        raise NotImplementedError("KIS provider 는 앱키 발급 후 구현한다 (05 §2.4)")
