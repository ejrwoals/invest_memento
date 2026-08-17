"""FRED — series/observations (05 §2.2).

거시 계열이므로 high·low 는 항상 None. 월·분기 계열도 매일 조회한다 — 발표 지연·
수정치 반영을 공짜로 얻는다. 값 '.' 은 결측(휴장 등) — 행을 만들지 않는다.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from app.series.providers import DailyBar
from app.series.providers._http import get_json

_BASE = "https://api.stlouisfed.org/fred/series/observations"


class FredProvider:
    name = "fred"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def fetch_daily(self, code: str, start: date, end: date) -> list[DailyBar]:
        payload = get_json(
            _BASE,
            params={
                "series_id": code,
                "api_key": self._api_key,
                "file_type": "json",
                "observation_start": start.isoformat(),
                "observation_end": end.isoformat(),
            },
        )
        today = date.today()
        bars: list[DailyBar] = []
        for row in payload.get("observations", []):
            try:
                close = Decimal(row["value"])
            except (InvalidOperation, KeyError):
                continue  # '.' = 결측
            d = date.fromisoformat(row["date"])
            if d >= today:  # 미마감 당일 방어 — 발표 특성상 실제로는 오지 않는다
                continue
            bars.append(DailyBar(date=d, close=close, high=None, low=None))
        return bars
