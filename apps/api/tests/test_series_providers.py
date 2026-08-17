"""provider 어댑터 — 외부 API 는 mock (05 §2).

fred·ecos 는 응답 파싱과 요청 구성을, kis 는 스텁임을, yfinance 는 코드 매핑을 본다.
"""

from datetime import date, timedelta
from decimal import Decimal as D
from typing import Any

import pytest

import app.series.providers.ecos as ecos_module
import app.series.providers.fred as fred_module
from app.series.providers import build_providers
from app.series.providers.ecos import EcosProvider
from app.series.providers.fred import FredProvider
from app.series.providers.kis import KisProvider
from app.series.providers.yfinance_dev import map_code

TODAY = date.today()


# ── FRED ────────────────────────────────────────────────────────────────────


def test_fred_parses_observations_and_skips_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get_json(url: str, params: dict[str, Any] | None = None) -> Any:
        captured["url"], captured["params"] = url, params
        return {
            "observations": [
                {"date": "2026-08-10", "value": "5.33"},
                {"date": "2026-08-11", "value": "."},  # 결측 — 행을 만들지 않는다
                {"date": "2026-08-12", "value": "5.35"},
            ]
        }

    monkeypatch.setattr(fred_module, "get_json", fake_get_json)
    bars = FredProvider("test-key").fetch_daily("DFF", date(2026, 8, 10), date(2026, 8, 12))
    assert captured["params"]["series_id"] == "DFF"
    assert captured["params"]["observation_start"] == "2026-08-10"
    assert [b["date"] for b in bars] == [date(2026, 8, 10), date(2026, 8, 12)]
    assert bars[0]["close"] == D("5.33")
    assert bars[0]["high"] is None and bars[0]["low"] is None  # 거시 계열


def test_fred_drops_today(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_json(url: str, params: dict[str, Any] | None = None) -> Any:
        return {"observations": [{"date": TODAY.isoformat(), "value": "5.0"}]}

    monkeypatch.setattr(fred_module, "get_json", fake_get_json)
    assert FredProvider("k").fetch_daily("DFF", TODAY - timedelta(days=1), TODAY) == []


# ── ECOS ────────────────────────────────────────────────────────────────────


def test_ecos_builds_path_from_composite_code(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get_json(url: str, params: dict[str, Any] | None = None) -> Any:
        captured["url"] = url
        return {
            "StatisticSearch": {
                "row": [
                    {"TIME": "20260810", "DATA_VALUE": "3.50"},
                    {"TIME": "20260811", "DATA_VALUE": "3.50"},
                ]
            }
        }

    monkeypatch.setattr(ecos_module, "get_json", fake_get_json)
    bars = EcosProvider("test-key").fetch_daily(
        "722Y001/D/0101000", date(2026, 8, 10), date(2026, 8, 11)
    )
    assert "/722Y001/D/20260810/20260811/0101000" in captured["url"]
    assert [b["date"] for b in bars] == [date(2026, 8, 10), date(2026, 8, 11)]
    assert bars[0]["close"] == D("3.50")
    assert bars[0]["high"] is None  # 발표된 값 하나가 그날의 값


def test_ecos_monthly_time_maps_to_first_day(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_json(url: str, params: dict[str, Any] | None = None) -> Any:
        return {"StatisticSearch": {"row": [{"TIME": "202607", "DATA_VALUE": "114.2"}]}}

    monkeypatch.setattr(ecos_module, "get_json", fake_get_json)
    bars = EcosProvider("k").fetch_daily("901Y009/M/0", date(2026, 7, 1), date(2026, 8, 1))
    assert bars[0]["date"] == date(2026, 7, 1)


def test_ecos_no_data_is_empty_and_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_data(url: str, params: dict[str, Any] | None = None) -> Any:
        return {"RESULT": {"CODE": "INFO-200", "MESSAGE": "데이터 없음"}}

    monkeypatch.setattr(ecos_module, "get_json", no_data)
    assert EcosProvider("k").fetch_daily("722Y001/D/0101000", TODAY, TODAY) == []

    def error(url: str, params: dict[str, Any] | None = None) -> Any:
        return {"RESULT": {"CODE": "ERROR-100", "MESSAGE": "인증 오류"}}

    monkeypatch.setattr(ecos_module, "get_json", error)
    with pytest.raises(RuntimeError):
        EcosProvider("k").fetch_daily("722Y001/D/0101000", TODAY, TODAY)


def test_ecos_rejects_malformed_code() -> None:
    with pytest.raises(ValueError):
        EcosProvider("k").fetch_daily("722Y001", TODAY, TODAY)


# ── KIS 스텁·yfinance 매핑·레지스트리 ───────────────────────────────────────


def test_kis_is_a_stub_until_app_key() -> None:
    with pytest.raises(NotImplementedError):
        KisProvider().fetch_daily("005930", TODAY - timedelta(days=1), TODAY)


def test_yfinance_code_mapping() -> None:
    assert map_code("005930") == "005930.KS"  # 국내 주식
    assert map_code("0001") == "^KS11"  # 코스피
    assert map_code("1001") == "^KQ11"  # 코스닥
    assert map_code("SPX") == "^GSPC"
    assert map_code("COMP") == "^IXIC"
    assert map_code("AAPL") == "AAPL"  # 미국 주식은 그대로


def test_registry_skips_missing_keys_and_respects_dev_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "fred_api_key", "")
    monkeypatch.setattr(settings, "ecos_api_key", "x")
    monkeypatch.setattr(settings, "series_dev_provider", "")
    providers = build_providers()
    assert "fred" not in providers  # 키 없으면 스킵하고 로그만
    assert isinstance(providers["ecos"], EcosProvider)
    assert isinstance(providers["kis"], KisProvider)

    monkeypatch.setattr(settings, "series_dev_provider", "yfinance")
    dev = build_providers()
    assert type(dev["kis"]).__name__ == "YfinanceDevProvider"  # 플래그가 kis 자리를 바꾼다
    assert dev["kis"].name == "kis"

    monkeypatch.setattr(settings, "series_dev_provider", "")
    assert isinstance(build_providers()["kis"], KisProvider)  # 플래그만 되돌리면 끝 (§2.5)
