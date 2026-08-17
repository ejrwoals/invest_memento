"""LLM 호출 래퍼 — 모든 에이전트의 단일 진입점 (04-ai-agents §3.1).

- 재시도: SDK 기본(429·5xx 지수 백오프, max_retries=2)을 그대로 쓴다.
  같은 일을 여기서 다시 구현하지 않는다.
- 타임아웃: 작업별로 호출부가 `for_task()` 로 지정한다 (대화 스트리밍 짧게, 조립 길게).
- 토큰 로깅: 호출마다 usage·request_id·에이전트 이름·프롬프트 버전을 구조화 로그로
  남긴다 — 비용 가드(§3.5)와 계측(§6)의 원천 데이터.
"""

import logging
from functools import lru_cache

from anthropic import AsyncAnthropic
from anthropic.types import Usage

from app.config import settings

logger = logging.getLogger("app.agents")


@lru_cache(maxsize=1)
def get_client() -> AsyncAnthropic:
    # api_key 가 비면 SDK 의 환경 변수 해석(ANTHROPIC_API_KEY)에 맡긴다
    return AsyncAnthropic(api_key=settings.anthropic_api_key or None, max_retries=2)


def for_task(timeout_seconds: float) -> AsyncAnthropic:
    """작업별 타임아웃을 입힌 클라이언트. 클라이언트 본체는 재사용된다."""
    return get_client().with_options(timeout=timeout_seconds)


def log_usage(
    *,
    agent: str,
    model: str,
    prompt_version: int,
    usage: Usage,
    request_id: str | None,
) -> None:
    """호출 1건의 토큰 사용량 구조화 로그 — 캐시 적중 확인(cache_read)도 여기서 본다."""
    logger.info(
        "llm_usage agent=%s model=%s prompt_version=%d request_id=%s "
        "input=%d output=%d cache_read=%s cache_creation=%s",
        agent,
        model,
        prompt_version,
        request_id,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_input_tokens,
        usage.cache_creation_input_tokens,
    )
