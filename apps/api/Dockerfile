# Cloud Run용 이미지. api·worker가 같은 이미지를 쓰고,
# worker는 Cloud Run 서비스의 command 오버라이드로 `python -m app.worker`를 실행한다.
# 배경: docs/dev/06-infra.md §3
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# 의존성 레이어 분리 — 코드만 바뀌면 sync 레이어는 캐시된다
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY app ./app

# Cloud Run이 PORT를 주입한다 (기본 8080)
CMD ["sh", "-c", "uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
