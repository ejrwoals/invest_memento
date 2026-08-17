"""Supabase Auth JWT 검증.

웹은 Supabase Auth로 로그인하고, 모든 API 요청에 access token을 Bearer로 첨부한다.
서버는 토큰을 로컬에서 검증한다 — 비대칭 키 프로젝트는 JWKS(캐시), 레거시 프로젝트는
HS256 공유 시크릿(SUPABASE_JWT_SECRET). 근거: docs/dev/02-backend.md §3.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"{settings.supabase_url}/auth/v1/.well-known/jwks.json",
        cache_keys=True,
    )


def _decode(token: str) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    if header.get("alg") == "HS256":
        if not settings.supabase_jwt_secret:
            raise jwt.InvalidTokenError("HS256 token but SUPABASE_JWT_SECRET is not set")
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    key = _jwks_client().get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["ES256", "RS256"], audience="authenticated")


def current_user(
    cred: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if cred is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        claims = _decode(cred.credentials)
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail="invalid token") from e
    return CurrentUser(id=claims["sub"], email=claims.get("email"))


RequireUser = Annotated[CurrentUser, Depends(current_user)]
