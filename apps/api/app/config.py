from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    # 레거시 HS256 프로젝트용. 비어 있으면 JWKS(비대칭 키)로 검증한다.
    supabase_jwt_secret: str = ""
    # Supabase Supavisor transaction pooler 접속 문자열 (대시보드 Connect → Transaction pooler)
    database_url: str = ""
    cors_origins: list[str] = ["http://localhost:3003", "http://127.0.0.1:3003"]
    # Claude API — 비어 있으면 SDK 기본 해석(ANTHROPIC_API_KEY env)을 따른다
    anthropic_api_key: str = ""
    # M4 수치 축 — 키가 비어 있으면 해당 provider 는 수집에서 스킵된다 (05 §8)
    fred_api_key: str = ""
    ecos_api_key: str = ""
    # 개발 플래그: 'yfinance' 면 kis 자리에 yfinance 어댑터를 끼운다 (05 §2.5, 출시 전 제거)
    series_dev_provider: str = ""


settings = Settings()
