from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_me_requires_token() -> None:
    assert client.get("/me").status_code == 401


def test_me_rejects_garbage_token() -> None:
    res = client.get("/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert res.status_code == 401
