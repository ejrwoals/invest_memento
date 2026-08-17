"""ECOS (한국은행) — StatisticSearch (05 §2.3).

`series_catalog.code`는 `통계표코드/주기/항목코드1[/항목코드2]`를 하나의 문자열로
합친 것이다. 거시·환율 계열이므로 high·low 는 None — "발표된 값 하나가 그날의 값".
월·분기 계열의 date 는 기간의 첫날로 둔다 (FRED 관례와 통일).
"""

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.series.providers import DailyBar
from app.series.providers._http import get_json

logger = logging.getLogger(__name__)

_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
_MAX_ROWS = 10000


def _format_period(cycle: str, d: date) -> str:
    if cycle == "D":
        return d.strftime("%Y%m%d")
    if cycle == "M":
        return d.strftime("%Y%m")
    if cycle == "Q":
        return f"{d.year}Q{(d.month - 1) // 3 + 1}"
    return str(d.year)  # A


def _parse_time(cycle: str, value: str) -> date:
    if cycle == "D":
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    if cycle == "M":
        return date(int(value[:4]), int(value[4:6]), 1)
    if cycle == "Q":
        quarter = int(value[-1])  # 'YYYYQn'
        return date(int(value[:4]), (quarter - 1) * 3 + 1, 1)
    return date(int(value[:4]), 1, 1)  # A


class EcosProvider:
    name = "ecos"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def fetch_daily(self, code: str, start: date, end: date) -> list[DailyBar]:
        parts = code.split("/")
        if len(parts) < 3:
            raise ValueError(f"ecos 코드 형식 오류 (통계표코드/주기/항목코드1[/항목코드2]): {code}")
        stat_code, cycle, *items = parts
        cycle = cycle.upper()
        path = "/".join(
            [
                self._api_key,
                "json",
                "kr",
                "1",
                str(_MAX_ROWS),
                stat_code,
                cycle,
                _format_period(cycle, start),
                _format_period(cycle, end),
                *items,
            ]
        )
        payload: Any = get_json(f"{_BASE}/{path}")
        body = payload.get("StatisticSearch")
        if body is None:
            # 데이터 없음(INFO-200)은 빈 목록 — 그 외 오류 코드는 예외로 올린다
            result = payload.get("RESULT", {})
            if result.get("CODE") == "INFO-200":
                return []
            raise RuntimeError(f"ecos 오류 응답: {result.get('CODE')} {result.get('MESSAGE')}")
        today = date.today()
        bars: list[DailyBar] = []
        for row in body.get("row", []):
            try:
                close = Decimal(str(row["DATA_VALUE"]))
            except (InvalidOperation, KeyError, TypeError):
                continue  # 결측
            d = _parse_time(cycle, str(row["TIME"]))
            if cycle == "D" and d >= today:
                continue  # 미마감 당일 방어
            bars.append(DailyBar(date=d, close=close, high=None, low=None))
        return bars
