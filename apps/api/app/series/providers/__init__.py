"""Provider 추상화 — 모든 수치 소스는 이 인터페이스 뒤에 있다 (05 §2).

무료 API 의 티어 정책은 예고 없이 바뀌므로 특정 제공자에 종속되는 코드를 만들지
않는다. 미마감 당일을 걸러낼 책임은 구현체에 있다 — 호출자는 받은 것을 그대로
upsert 한다. 레이트 리밋·인증·재시도도 구현체 내부의 일이다.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Protocol, TypedDict

from app.config import settings

logger = logging.getLogger(__name__)


class DailyBar(TypedDict):
    date: date  # 현지 거래일
    close: Decimal
    high: Decimal | None  # 거시 계열은 None
    low: Decimal | None


class SeriesProvider(Protocol):
    name: str  # 'fred' | 'ecos' | 'kis'

    def fetch_daily(self, code: str, start: date, end: date) -> list[DailyBar]:
        """확정된 일별 값만. 미마감 당일은 포함하지 않는다."""
        ...


def build_providers() -> dict[str, SeriesProvider]:
    """가용한 provider 만 담은 레지스트리를 만든다.

    - 키가 없는 fred·ecos 는 빠진다 — 수집은 해당 계열만 스킵하고 로그를 남긴다 (05 §8).
    - 개발 플래그 series_dev_provider='yfinance' 면 kis 자리에 yfinance 어댑터를
      끼운다 (05 §2.5). KIS 준비 즉시 플래그만 되돌린다 — 다른 코드는 건드리지 않는다.
    """
    from app.series.providers.ecos import EcosProvider
    from app.series.providers.fred import FredProvider

    providers: dict[str, SeriesProvider] = {}
    if settings.fred_api_key:
        providers["fred"] = FredProvider(settings.fred_api_key)
    else:
        logger.info("FRED_API_KEY 없음 — fred 계열 수집을 스킵한다")
    if settings.ecos_api_key:
        providers["ecos"] = EcosProvider(settings.ecos_api_key)
    else:
        logger.info("ECOS_API_KEY 없음 — ecos 계열 수집을 스킵한다")
    if settings.series_dev_provider == "yfinance":
        from app.series.providers.yfinance_dev import YfinanceDevProvider

        providers["kis"] = YfinanceDevProvider()
        logger.warning("개발 플래그: kis provider 자리에 yfinance 어댑터 사용 중 (출시 전 제거)")
    else:
        from app.series.providers.kis import KisProvider

        providers["kis"] = KisProvider()
    return providers
