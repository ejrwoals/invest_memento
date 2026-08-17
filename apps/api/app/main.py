from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import RequireUser
from app.config import settings
from app.routers import notes

app = FastAPI(title="Investment Memento API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(notes.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
def me(user: RequireUser) -> dict[str, str | None]:
    return {"user_id": user.id, "email": user.email}
