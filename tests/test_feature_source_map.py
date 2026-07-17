from __future__ import annotations

import json
from pathlib import Path

from agents.model_agent import ONCHAIN_HINTS, ModelAgent
from features.feature_source_map import (
    FEATURE_SOURCE_MAP_FILENAME,
    build_feature_source_map_from_hints,
    classify_feature_source_by_hints,
    load_feature_source_map,
    write_feature_source_map,
)
from tests.test_model_agent_research_mode import _cfg, _write_model_inputs


def _map_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "features" / FEATURE_SOURCE_MAP_FILENAME


def _prepared_agent(tmp_path: Path) -> ModelAgent:
    agent = ModelAgent(_cfg(tmp_path))
    agent.prepare()
    return agent


def test_load_feature_source_map_accepts_wrapped_and_flat_formats(tmp_path):
    wrapped = tmp_path / "wrapped.json"
    write_feature_source_map(wrapped, {"tx_count": "onchain", "log_ret_1d": "market"}, generated_by="test")
    assert load_feature_source_map(wrapped) == {"tx_count": "onchain", "log_ret_1d": "market"}

    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({"tx_count": "onchain", "log_ret_1d": "market"}))
    assert load_feature_source_map(flat) == {"tx_count": "onchain", "log_ret_1d": "market"}


def test_load_feature_source_map_degrades_to_none(tmp_path):
    assert load_feature_source_map(tmp_path / "missing.json") is None
    garbage = tmp_path / "garbage.json"
    garbage.write_text("not json {")
    assert load_feature_source_map(garbage) is None
    # entries with invalid sources are dropped; all-invalid -> None (hints fallback)
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"tx_count": "labels"}))
    assert load_feature_source_map(invalid) is None


def test_model_agent_falls_back_to_hints_without_map(tmp_path):
    _write_model_inputs(tmp_path)
    agent = _prepared_agent(tmp_path)
    assert agent._feature_source_map == {}
    market = agent._select_feature_columns(agent._dataset, "market_only")
    onchain = agent._select_feature_columns(agent._dataset, "onchain_only")
    # hints classify tx_count / market_cap_usd as onchain
    assert "tx_count" not in market and "market_cap_usd" not in market
    assert {"tx_count", "market_cap_usd"}.issubset(set(onchain))
    assert "log_ret_1d" in market


def test_model_agent_map_overrides_hints_and_unmapped_falls_back(tmp_path):
    _write_model_inputs(tmp_path)
    # deliberately contradict the hints: tx_count tagged market, log_ret_1d tagged onchain;
    # market_cap_usd left unmapped so it must fall back to the hint classification (onchain).
    write_feature_source_map(
        _map_path(tmp_path),
        {"tx_count": "market", "log_ret_1d": "onchain"},
        generated_by="test",
    )
    agent = _prepared_agent(tmp_path)
    assert agent._feature_source_map == {"tx_count": "market", "log_ret_1d": "onchain"}
    market = agent._select_feature_columns(agent._dataset, "market_only")
    onchain = agent._select_feature_columns(agent._dataset, "onchain_only")
    assert "tx_count" in market and "tx_count" not in onchain
    assert "log_ret_1d" in onchain and "log_ret_1d" not in market
    assert "market_cap_usd" in onchain  # unmapped -> ONCHAIN_HINTS fallback


def test_backfilled_map_reproduces_hint_selection_exactly(tmp_path):
    """The backfill path (build map from hints, then select via map) must yield
    feature sets identical to pure hint-based selection — the item [4] guarantee."""
    _write_model_inputs(tmp_path)
    baseline = _prepared_agent(tmp_path)
    expected = {fs: baseline._select_feature_columns(baseline._dataset, fs)
                for fs in ["market_only", "onchain_only", "market_plus_onchain"]}

    columns = [c for c in baseline._dataset.columns if c not in {"date_ts", "symbol"}]
    write_feature_source_map(
        _map_path(tmp_path),
        build_feature_source_map_from_hints(columns, ONCHAIN_HINTS),
        generated_by="test-backfill",
    )
    agent = _prepared_agent(tmp_path)
    assert agent._feature_source_map  # map actually loaded
    for fs, cols in expected.items():
        assert agent._select_feature_columns(agent._dataset, fs) == cols


def test_classify_feature_source_by_hints():
    assert classify_feature_source_by_hints("tx_count_growth_7d", ONCHAIN_HINTS) == "onchain"
    assert classify_feature_source_by_hints("log_ret_7d", ONCHAIN_HINTS) == "market"
