from __future__ import annotations

from pathlib import Path

import pytest

from providers.coinmarketcap import CoinMarketCapProvider, CoinMarketCapProviderError
from providers.http_client import params_hash


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cmc"


def test_cmc_provider_requires_api_key_when_live_enabled(monkeypatch, tmp_path):
    monkeypatch.delenv("CMC_API_KEY", raising=False)
    provider = CoinMarketCapProvider(cache_dir=tmp_path, live_api_enabled=True)
    with pytest.raises(CoinMarketCapProviderError) as exc:
        provider.fetch_map(symbols=["BTC"], live_api_enabled=True)
    assert "CMC_API_KEY is missing" in str(exc.value)


def test_cmc_provider_allows_fixtures_without_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("CMC_API_KEY", raising=False)
    provider = CoinMarketCapProvider(cache_dir=tmp_path, live_api_enabled=False)
    df = provider.fetch_map(fixture_path=FIXTURES / "map_sample.json", live_api_enabled=False)
    assert set(df["symbol"]) == {"BTC", "ETH"}


def test_cmc_historical_listings_fixture_parse(tmp_path):
    provider = CoinMarketCapProvider(cache_dir=tmp_path, live_api_enabled=False)
    df = provider.fetch_historical_listings(
        "2024-01-01",
        fixture_path=FIXTURES / "listings_historical_sample.json",
        live_api_enabled=False,
    )
    assert {"cmc_id", "symbol", "slug", "market_cap_usd"}.issubset(df.columns)
    assert list(df["symbol"])[:2] == ["BTC", "ETH"]


def test_cmc_historical_listings_uses_yyyy_mm_dd_date_param(tmp_path, monkeypatch):
    provider = CoinMarketCapProvider(cache_dir=tmp_path, live_api_enabled=False)
    captured = {}

    def fake_get_json(endpoint, params, **kwargs):
        captured["endpoint"] = endpoint
        captured["params"] = params
        return {"data": []}

    monkeypatch.setattr(provider, "_get_json", fake_get_json)
    provider.fetch_historical_listings("2024-01-15T12:34:56Z", live_api_enabled=False)
    assert captured["endpoint"] == "/v1/cryptocurrency/listings/historical"
    assert captured["params"]["date"] == "2024-01-15"


def test_cmc_historical_listings_does_not_send_include_inactive_or_empty_convert_id(tmp_path, monkeypatch):
    provider = CoinMarketCapProvider(cache_dir=tmp_path, live_api_enabled=False)
    captured = {}

    def fake_get_json(endpoint, params, **kwargs):
        captured["params"] = params
        return {"data": []}

    monkeypatch.setattr(provider, "_get_json", fake_get_json)
    provider.fetch_historical_listings("2024-01-01", live_api_enabled=False)
    assert "include_inactive" not in captured["params"]
    assert "convert_id" not in captured["params"]
    assert captured["params"]["cryptocurrency_type"] == "all"


def test_cmc_ohlcv_fixture_parse(tmp_path):
    provider = CoinMarketCapProvider(cache_dir=tmp_path, live_api_enabled=False)
    df = provider.fetch_ohlcv_historical(
        1,
        "BTC",
        "2024-01-01",
        "2024-01-03",
        fixture_path=FIXTURES / "ohlcv_historical_sample.json",
        live_api_enabled=False,
    )
    assert len(df) == 2
    assert set(["cmc_id", "symbol", "open", "high", "low", "close", "volume", "market_cap"]).issubset(df.columns)
    assert (df["close"] > 0).all()


def test_cmc_uses_cache_before_live_api(monkeypatch, tmp_path):
    monkeypatch.setenv("CMC_API_KEY", "secret-key")
    provider = CoinMarketCapProvider(cache_dir=tmp_path, live_api_enabled=True)
    payload = provider.fetch_map(fixture_path=FIXTURES / "map_sample.json", live_api_enabled=False)
    assert not payload.empty
    cache_path = provider.http.cache_path("coinmarketcap", f"v1_cryptocurrency_map_{params_hash({})}")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text((FIXTURES / "map_sample.json").read_text())

    def fail_get(*args, **kwargs):
        raise AssertionError("live API should not be called on cache hit")

    monkeypatch.setattr(provider.http.session, "get", fail_get)
    df = provider.fetch_map(live_api_enabled=True)
    assert not df.empty
    assert provider.http.cache_hit_count_by_provider["coinmarketcap"] >= 1


def test_cmc_does_not_log_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("CMC_API_KEY", "super-secret-value")
    provider = CoinMarketCapProvider(cache_dir=tmp_path, live_api_enabled=True)
    headers = provider._headers()
    assert headers["X-CMC_PRO_API_KEY"] == "super-secret-value"
    cache_path = provider.http.cache_path("coinmarketcap", f"v1_cryptocurrency_map_{params_hash({})}")
    assert "super-secret-value" not in str(cache_path)
