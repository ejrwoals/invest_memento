"""계열 조회 라우터 — 카탈로그 검색과 스냅샷 구간 (05 §3.2, 차트용)."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import CurrentUser, current_user
from app.db.models import Instrument, SeriesCatalogEntry, SeriesSnapshot
from app.db.session import get_session
from app.main import app

USER = CurrentUser(id="11111111-1111-1111-1111-111111111111", email="dev@example.com")


@pytest.fixture()
def client(series_db: async_sessionmaker[AsyncSession]) -> Iterator[TestClient]:
    async def _seed() -> None:
        async with series_db() as s:
            s.add_all(
                [
                    SeriesCatalogEntry(
                        provider="fred",
                        code="DFF",
                        label="미국 기준금리(실효 연방기금금리)",
                        kind="macro",
                        unit="%",
                        has_intraday=False,
                        search_keywords=["미국 기준금리", "연준", "Fed"],
                    ),
                    SeriesCatalogEntry(
                        provider="ecos",
                        code="722Y001/D/0101000",
                        label="한국 기준금리",
                        kind="macro",
                        unit="%",
                        has_intraday=False,
                        search_keywords=["한국은행", "기준금리"],
                    ),
                    SeriesCatalogEntry(
                        provider="kis",
                        code="0001",
                        label="코스피",
                        kind="index",
                        has_intraday=True,
                        search_keywords=["KOSPI"],
                    ),
                    # instruments 에도 있는 등록 종목 — 검색은 카탈로그 행을 정본으로 낸다
                    SeriesCatalogEntry(
                        provider="kis",
                        code="005930",
                        label="삼성전자",
                        kind="equity",
                        has_intraday=True,
                        search_keywords=["삼성전자"],
                    ),
                ]
            )
            s.add_all(
                [
                    Instrument(symbol="005930", name="삼성전자", market="kr", currency="KRW"),
                    # 카탈로그 미등록 — instruments 매칭으로만 나온다
                    Instrument(symbol="MCD", name="맥도날드", market="us", currency="USD"),
                ]
            )
            s.add_all(
                [
                    SeriesSnapshot(
                        provider="ecos",
                        code="722Y001/D/0101000",
                        date=date(2026, 8, d),
                        close=D("3.50"),
                    )
                    for d in (10, 11, 12)
                ]
            )
            await s.commit()

    asyncio.run(_seed())

    async def _session() -> AsyncIterator[AsyncSession]:
        async with series_db() as s:
            yield s

    app.dependency_overrides[current_user] = lambda: USER
    app.dependency_overrides[get_session] = _session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_search_matches_label_and_keywords(client: TestClient) -> None:
    body = client.get("/series/search", params={"q": "기준금리"}).json()
    assert {e["code"] for e in body} == {"DFF", "722Y001/D/0101000"}

    body = client.get("/series/search", params={"q": "fed"}).json()  # 대소문자 무시
    assert [e["code"] for e in body] == ["DFF"]

    assert client.get("/series/search", params={"q": "없는말"}).json() == []


def test_search_hits_instruments_not_in_catalog(client: TestClient) -> None:
    """카탈로그 미등록 종목도 instruments 매칭으로 나온다 — 참조 시 자동 등록의 입구."""
    body = client.get("/series/search", params={"q": "맥도"}).json()  # name 매칭
    assert body == [
        {
            "provider": "kis",
            "code": "MCD",
            "label": "맥도날드",
            "kind": "equity",
            "unit": None,
            "has_intraday": True,
            "unregistered": True,
        }
    ]

    body = client.get("/series/search", params={"q": "mcd"}).json()  # symbol·대소문자 무시
    assert [e["code"] for e in body] == ["MCD"]


def test_search_dedupes_catalog_and_instruments(client: TestClient) -> None:
    # 카탈로그·instruments 에 모두 있는 종목은 카탈로그 행 1건 — unregistered=False
    body = client.get("/series/search", params={"q": "삼성전자"}).json()
    assert [(e["code"], e["unregistered"]) for e in body] == [("005930", False)]

    # 텍스트 매칭은 빗나가고 symbol 로만 잡혀도 카탈로그 행이 정본이다
    body = client.get("/series/search", params={"q": "005930"}).json()
    assert [(e["kind"], e["unregistered"]) for e in body] == [("equity", False)]


def test_search_synthesizes_ticker_candidate(client: TestClient) -> None:
    """카탈로그·instruments 둘 다 미스인 티커 모양 쿼리는 합성 후보 1건."""
    body = client.get("/series/search", params={"q": "tsla"}).json()  # 영문 1~6자 → 대문자화
    assert body == [
        {
            "provider": "kis",
            "code": "TSLA",
            "label": "TSLA (새 종목)",
            "kind": "equity",
            "unit": None,
            "has_intraday": True,
            "unregistered": True,
        }
    ]

    body = client.get("/series/search", params={"q": "123456"}).json()  # 숫자 6자리도 티커 모양
    assert [(e["code"], e["unregistered"]) for e in body] == [("123456", True)]

    # 티커 모양이 아니면 합성하지 않는다 (7자 영문·한글)
    assert client.get("/series/search", params={"q": "ABCDEFG"}).json() == []
    assert client.get("/series/search", params={"q": "없는종목"}).json() == []


def test_series_range_supports_slash_codes_and_bounds(client: TestClient) -> None:
    # ecos 코드는 슬래시를 품는다 — path 컨버터 경로 확인
    response = client.get(
        "/series/ecos/722Y001/D/0101000",
        params={"from": "2026-08-11", "to": "2026-08-12"},
    )
    assert response.status_code == 200
    body = response.json()
    assert [row["date"] for row in body] == ["2026-08-11", "2026-08-12"]
    assert body[0]["close"] == "3.5000"
    assert body[0]["high"] is None


def test_series_without_bounds_returns_all(client: TestClient) -> None:
    body = client.get("/series/ecos/722Y001/D/0101000").json()
    assert len(body) == 3


def test_unknown_series_is_404(client: TestClient) -> None:
    assert client.get("/series/kis/999999").status_code == 404
