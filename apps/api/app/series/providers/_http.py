"""provider 공용 HTTP 헬퍼 — 일시 오류(429·5xx)는 지수 백오프 2회 재시도 후 포기.

어차피 내일 소급 수집되므로(05 §8) 그 이상의 재시도 큐는 만들지 않는다.
"""

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_RETRYABLE = {429, 500, 502, 503, 504}
_TIMEOUT = 30.0


def get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    last_error: Exception | None = None
    for attempt in range(3):  # 최초 1회 + 재시도 2회
        if attempt:
            time.sleep(2**attempt)  # 2초, 4초
        try:
            response = httpx.get(url, params=params, timeout=_TIMEOUT)
            if response.status_code in _RETRYABLE:
                last_error = httpx.HTTPStatusError(
                    f"status {response.status_code}", request=response.request, response=response
                )
                continue
            response.raise_for_status()
            return response.json()
        except httpx.TransportError as e:  # 연결·타임아웃 — 일시 오류로 취급
            last_error = e
    assert last_error is not None
    raise last_error
