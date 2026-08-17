"""2단계 폼 엔드포인트 — auto 조건 설정·수정 이력과 지켜보는 수치 (ux §3.3).

sqlite 파일 DB(conftest.series_db) 위에서 라우터 경로를 실제로 호출한다.
user 스코핑(남의 리소스는 404)·409(auto 아님)·422(조건 미완비·미등록 계열)·
auto_condition_edits 필드별 이력을 본다.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import CurrentUser, current_user
from app.db.models import (
    AutoConditionEdit,
    Galae,
    Instrument,
    Note,
    Scenario,
    SeriesCatalogEntry,
    Watch,
)
from app.db.session import get_session
from app.main import app

USER = CurrentUser(id="11111111-1111-1111-1111-111111111111", email="dev@example.com")
OTHER = CurrentUser(id="22222222-2222-2222-2222-222222222222", email="other@example.com")


def _note(user_id: str) -> Note:
    note = Note(
        user_id=uuid.UUID(user_id),
        target_type="ticker",
        target_symbol=None,
        target_name="삼성전자",
        thesis_summary="HBM4 진입이 리레이팅을 만든다",
        color="#2563eb",
    )
    galae = Galae(
        question="연말까지 95,000원을 넘는가?", judge_kind="date", judge_end=date(2026, 12, 31)
    )
    galae.scenarios = [
        Scenario(
            name="95,000원을 넘는다",
            resolution_type="auto",
            position=0,
            series_provider="kis",
            series_code="005930",
            series_label="삼성전자",
            comparator="gte",
            target_value=D("95000"),
            auto_status="pending",
            progress=0.4,
        ),
        Scenario(name="못 미친다", resolution_type="manual", position=1),
        Scenario(
            name="그 외 예상 못한 전개",
            resolution_type="complement",
            is_residual=True,
            position=2,
        ),
    ]
    note.galae = [galae]
    return note


@pytest.fixture()
def ctx(series_db: async_sessionmaker[AsyncSession]) -> Iterator[dict[str, str]]:
    """카탈로그 + 내 노트 + 남의 노트를 심고, 시나리오·노트 id 를 돌려준다."""
    ids: dict[str, str] = {}

    async def _seed() -> None:
        async with series_db() as s:
            s.add_all(
                [
                    SeriesCatalogEntry(
                        provider="kis",
                        code="005930",
                        label="삼성전자",
                        kind="stock",
                        has_intraday=True,
                        search_keywords=["삼성전자"],
                    ),
                    SeriesCatalogEntry(
                        provider="fred",
                        code="DFF",
                        label="미국 기준금리(실효 연방기금금리)",
                        kind="macro",
                        unit="%",
                        search_keywords=["기준금리"],
                    ),
                ]
            )
            # 카탈로그에 없는 kis 종목의 동적 등록 확인용 — instruments 에만 있다
            s.add(Instrument(symbol="000660", name="SK하이닉스", market="kr", currency="KRW"))
            mine, theirs = _note(USER.id), _note(OTHER.id)
            s.add_all([mine, theirs])
            await s.flush()
            ids["note"] = str(mine.id)
            ids["auto"] = str(mine.galae[0].scenarios[0].id)
            ids["manual"] = str(mine.galae[0].scenarios[1].id)
            ids["residual"] = str(mine.galae[0].scenarios[2].id)
            ids["their_auto"] = str(theirs.galae[0].scenarios[0].id)
            ids["their_note"] = str(theirs.id)
            await s.commit()

    asyncio.run(_seed())

    async def _session() -> AsyncIterator[AsyncSession]:
        async with series_db() as s:
            yield s

    app.dependency_overrides[current_user] = lambda: USER
    app.dependency_overrides[get_session] = _session
    yield ids
    app.dependency_overrides.clear()


def _resolution_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "series_provider": "kis",
        "series_code": "005930",
        "series_label": "삼성전자",
        "comparator": "gte",
        "target_value": "97000",
    }
    body.update(overrides)
    return body


# ── PATCH /scenarios/{id}/resolution ───────────────────────────────────────


def test_patch_resolution_updates_and_records_history(
    ctx: dict[str, str], series_db: async_sessionmaker[AsyncSession]
) -> None:
    with TestClient(app) as client:
        res = client.patch(
            f"/scenarios/{ctx['auto']}/resolution",
            json=_resolution_body(reason="목표를 올려 잡았다"),
        )
    assert res.status_code == 200
    body = res.json()
    assert body["target_value"] == "97000"
    # 조건이 바뀌면 평가 캐시는 무효가 된다 — 다음 배치가 다시 계산한다
    assert body["auto_status"] is None and body["progress"] is None

    async def _edits() -> list[AutoConditionEdit]:
        async with series_db() as s:
            return list(
                (
                    await s.scalars(
                        select(AutoConditionEdit).where(
                            AutoConditionEdit.scenario_id == uuid.UUID(ctx["auto"])
                        )
                    )
                ).all()
            )

    edits = asyncio.run(_edits())
    # 달라진 필드(target_value)만 이력에 남는다 — provider·comparator 는 그대로였다
    assert [(e.field, e.from_value, e.to_value) for e in edits] == [
        ("target_value", "95000", "97000")
    ]
    assert edits[0].reason == "목표를 올려 잡았다"


def test_patch_resolution_noop_keeps_cache_and_history_empty(
    ctx: dict[str, str], series_db: async_sessionmaker[AsyncSession]
) -> None:
    with TestClient(app) as client:
        res = client.patch(
            f"/scenarios/{ctx['auto']}/resolution", json=_resolution_body(target_value="95000")
        )
    assert res.status_code == 200
    assert res.json()["auto_status"] == "pending"  # 아무것도 안 바뀌면 캐시도 그대로

    async def _count() -> int:
        async with series_db() as s:
            return len((await s.scalars(select(AutoConditionEdit))).all())

    assert asyncio.run(_count()) == 0


def test_patch_resolution_rejects_non_auto(ctx: dict[str, str]) -> None:
    with TestClient(app) as client:
        for key in ("manual", "residual"):
            res = client.patch(f"/scenarios/{ctx[key]}/resolution", json=_resolution_body())
            assert res.status_code == 409
            assert res.json()["detail"]["code"] == "RESOLUTION_NOT_AUTO"


def test_patch_resolution_scopes_by_user(ctx: dict[str, str]) -> None:
    with TestClient(app) as client:
        # 남의 시나리오는 404 — 존재 여부를 흘리지 않는다
        assert (
            client.patch(
                f"/scenarios/{ctx['their_auto']}/resolution", json=_resolution_body()
            ).status_code
            == 404
        )
        missing = client.patch(f"/scenarios/{uuid.uuid4()}/resolution", json=_resolution_body())
        assert missing.status_code == 404


def test_patch_resolution_requires_complete_targets(ctx: dict[str, str]) -> None:
    with TestClient(app) as client:
        res = client.patch(
            f"/scenarios/{ctx['auto']}/resolution",
            json=_resolution_body(comparator="between", target_value=None),
        )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "AUTO_CONDITION_INCOMPLETE"


def test_patch_resolution_rejects_unknown_series(ctx: dict[str, str]) -> None:
    with TestClient(app) as client:
        # kis 가 아닌 provider 는 동적 등록이 없다 — 카탈로그에 없으면 422
        res = client.patch(
            f"/scenarios/{ctx['auto']}/resolution",
            json=_resolution_body(series_provider="fred", series_code="NOPE"),
        )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "UNKNOWN_SERIES"


def test_patch_resolution_registers_kis_equity(
    ctx: dict[str, str], series_db: async_sessionmaker[AsyncSession]
) -> None:
    """카탈로그에 없는 kis 종목을 auto 조건이 참조하면 kind='equity' 로 등록된다 (05 §3.1)."""
    with TestClient(app) as client:
        res = client.patch(
            f"/scenarios/{ctx['auto']}/resolution",
            json=_resolution_body(
                series_provider="kis", series_code="000660", series_label="SK하이닉스"
            ),
        )
    assert res.status_code == 200

    async def _entry() -> SeriesCatalogEntry | None:
        async with series_db() as s:
            return await s.scalar(
                select(SeriesCatalogEntry).where(
                    SeriesCatalogEntry.provider == "kis", SeriesCatalogEntry.code == "000660"
                )
            )

    entry = asyncio.run(_entry())
    assert entry is not None
    assert entry.kind == "equity" and entry.has_intraday
    assert entry.label == "SK하이닉스"  # instruments.name 에서 왔다


def test_watch_registers_kis_equity_with_code_label(
    ctx: dict[str, str], series_db: async_sessionmaker[AsyncSession]
) -> None:
    """instruments 에도 없는 kis 종목은 label=code 로라도 등록된다 — FK 가 먼저다."""
    with TestClient(app) as client:
        res = client.post(
            f"/notes/{ctx['note']}/watches",
            json={"provider": "kis", "code": "035420", "label": "네이버"},
        )
    assert res.status_code == 201

    async def _entry() -> SeriesCatalogEntry | None:
        async with series_db() as s:
            return await s.scalar(
                select(SeriesCatalogEntry).where(
                    SeriesCatalogEntry.provider == "kis", SeriesCatalogEntry.code == "035420"
                )
            )

    entry = asyncio.run(_entry())
    assert entry is not None
    assert entry.kind == "equity" and entry.label == "035420"


def test_watch_registers_instrument_and_catalog_for_new_symbol(
    ctx: dict[str, str], series_db: async_sessionmaker[AsyncSession]
) -> None:
    """instruments 에도 없는 새 심볼(검색 합성 후보)은 instruments 까지 만든다.

    market·currency 는 코드 형태 휴리스틱 — 영문=us/USD, 숫자=kr/KRW."""
    with TestClient(app) as client:
        us = client.post(
            f"/notes/{ctx['note']}/watches",
            json={"provider": "kis", "code": "MCD", "label": "MCD (새 종목)"},
        )
        kr = client.post(
            f"/notes/{ctx['note']}/watches",
            json={"provider": "kis", "code": "035420", "label": "네이버"},
        )
    assert us.status_code == 201 and kr.status_code == 201

    async def _rows() -> tuple[Instrument | None, Instrument | None, SeriesCatalogEntry | None]:
        async with series_db() as s:
            entry = await s.scalar(
                select(SeriesCatalogEntry).where(
                    SeriesCatalogEntry.provider == "kis", SeriesCatalogEntry.code == "MCD"
                )
            )
            return await s.get(Instrument, "MCD"), await s.get(Instrument, "035420"), entry

    mcd, naver, entry = asyncio.run(_rows())
    assert mcd is not None and mcd.market == "us" and mcd.currency == "USD"
    assert naver is not None and naver.market == "kr" and naver.currency == "KRW"
    assert entry is not None and entry.kind == "equity" and entry.label == "MCD"


# ── watches ────────────────────────────────────────────────────────────────


def test_watch_create_appears_in_note_detail_and_deletes(ctx: dict[str, str]) -> None:
    with TestClient(app) as client:
        res = client.post(
            f"/notes/{ctx['note']}/watches",
            json={"provider": "fred", "code": "DFF", "label": "미국 기준금리"},
        )
        assert res.status_code == 201
        watch = res.json()
        assert watch["provider"] == "fred" and watch["label"] == "미국 기준금리"

        detail = client.get(f"/notes/{ctx['note']}").json()
        assert [w["id"] for w in detail["watches"]] == [watch["id"]]

        assert client.delete(f"/watches/{watch['id']}").status_code == 204
        assert client.get(f"/notes/{ctx['note']}").json()["watches"] == []


def test_watch_requires_known_series(ctx: dict[str, str]) -> None:
    with TestClient(app) as client:
        # kis 가 아닌 provider 는 동적 등록이 없다 — 카탈로그에 없으면 422
        res = client.post(
            f"/notes/{ctx['note']}/watches",
            json={"provider": "ecos", "code": "000000", "label": "없는 계열"},
        )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "UNKNOWN_SERIES"


def test_watch_scopes_by_user(
    ctx: dict[str, str], series_db: async_sessionmaker[AsyncSession]
) -> None:
    with TestClient(app) as client:
        # 남의 노트에는 담을 수 없다 — 404
        res = client.post(
            f"/notes/{ctx['their_note']}/watches",
            json={"provider": "fred", "code": "DFF", "label": "미국 기준금리"},
        )
        assert res.status_code == 404

        # 남의 watch 는 지울 수 없다 — 404
        async def _their_watch() -> str:
            async with series_db() as s:
                watch = Watch(
                    note_id=uuid.UUID(ctx["their_note"]),
                    provider="fred",
                    code="DFF",
                    label="미국 기준금리",
                )
                s.add(watch)
                await s.commit()
                return str(watch.id)

        their_watch = asyncio.run(_their_watch())
        assert client.delete(f"/watches/{their_watch}").status_code == 404
