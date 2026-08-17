"""yfinance 어댑터 — 개발 전용 (05 §2.5).

Yahoo 비공식 엔드포인트라 유료화 시 약관 위반이고 예고 없이 깨진다. 파이프라인
검증용으로만 쓰고 출시 전 반드시 제거한다. yfinance import 는 이 파일 밖으로
나가지 않는다. `series_catalog.provider` 에 'yfinance' 값은 없다 — 개발 환경에서
설정 플래그(series_dev_provider)로 kis 자리에 끼워진다. name='kis' 인 이유다.

KIS 코드 → Yahoo 심볼 매핑은 어댑터 내부의 일이다: 지수는 아래 표, 6자리 숫자는
국내 주식(`.KS` 접미), 나머지는 미국 티커 그대로.
"""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.series.providers import DailyBar

logger = logging.getLogger(__name__)

# 시드(010)의 kis 지수 코드 → Yahoo 심볼
_INDEX_MAP = {
    "0001": "^KS11",  # 코스피
    "1001": "^KQ11",  # 코스닥
    "SPX": "^GSPC",  # S&P500
    "COMP": "^IXIC",  # 나스닥
}


def map_code(code: str) -> str:
    if code in _INDEX_MAP:
        return _INDEX_MAP[code]
    if code.isdigit() and len(code) == 6:
        return f"{code}.KS"  # 국내 주식 — 예: 005930 → 005930.KS
    return code  # 미국 주식 티커는 그대로 (AAPL 등)


class YfinanceDevProvider:
    name = "kis"  # kis 자리에 끼워지는 대역이다

    def fetch_daily(self, code: str, start: date, end: date) -> list[DailyBar]:
        import yfinance as yf  # 어댑터 밖으로 새지 않는다

        ticker = yf.Ticker(map_code(code))
        # end 는 exclusive — 요청 구간 [start, end] 를 덮도록 하루 더한다
        frame = ticker.history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
        )
        if frame.empty:
            return []

        # 미마감 당일 제외 — 현지(거래소) 기준 오늘 날짜의 봉은 정규장 종료가 확인될
        # 때만 확정으로 본다. 확인 불가면 버린다 — 내일 소급 수집되므로 무손실이다.
        tz = frame.index.tz
        now_local = datetime.now(tz) if tz is not None else datetime.now()
        today_local = now_local.date()
        regular_end = (
            ticker.history_metadata.get("currentTradingPeriod", {}).get("regular", {}).get("end")
        )

        bars: list[DailyBar] = []
        for ts, row in frame.iterrows():
            d: date = ts.date()
            if d > end:
                continue
            if d >= today_local:
                closed = (
                    regular_end is not None
                    and regular_end.date() == d
                    and now_local >= regular_end
                )
                if not closed:
                    continue
            bars.append(
                DailyBar(
                    date=d,
                    close=Decimal(str(round(float(row["Close"]), 4))),
                    high=Decimal(str(round(float(row["High"]), 4))),
                    low=Decimal(str(round(float(row["Low"]), 4))),
                )
            )
        return bars
