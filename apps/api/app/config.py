from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    # 레거시 HS256 프로젝트용. 비어 있으면 JWKS(비대칭 키)로 검증한다.
    supabase_jwt_secret: str = ""
    # Supabase Supavisor transaction pooler 접속 문자열 (대시보드 Connect → Transaction pooler)
    database_url: str = ""
    cors_origins: list[str] = ["http://localhost:3003", "http://127.0.0.1:3003"]


settings = Settings()
