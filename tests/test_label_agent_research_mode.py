from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents.label_agent import LabelAgent, LabelAgentError
from configs.config import load_config
from scripts.verify_label_run import validate_label_outputs


def _cfg(tmp_path: Path) -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    labels = dict(cfg.get("labels", {}))
    labels.update(cfg.get("labels_smoke", {}))
    labels["output_dir"] = "data/labels_smoke"
    labels["min_symbols_required"] = 3
    labels["min_label_rows_required"] = 50
    labels["min_rows_per_horizon_required"] = 50
    labels["min_common_rows_all_horizons"] = 50
    labels["min_assets_per_label_date"] = 3
    cfg["labels"] = labels
    return cfg


def _write_upstream(
    tmp_path: Path,
    *,
    symbols: list[str],
    periods: int = 90,
    start: str = "2026-01-01",
    include_pruned: bool = True,
    bad_close: dict[tuple[str, int], float] | None = None,
    leakage_col: str | None = None,
) -> None:
    root = tmp_path / "data"
    (root / "raw" / "market").mkdir(parents=True, exist_ok=True)
    (root / "features").mkdir(parents=True, exist_ok=True)
    dates = pd.date_range(start, periods=periods, freq="D", tz="UTC")

    market_rows = []
    for sym_idx, symbol in enumerate(symbols):
        base = 100 + sym_idx * 20
        for i, date_ts in enumerate(dates):
            close = base + i + sym_idx * 0.1
            if bad_close and (symbol, i) in bad_close:
                close = bad_close[(symbol, i)]
            market_rows.append(
                {
                    "date_ts": date_ts,
                    "symbol": symbol,
                    "exchange": "coinbase",
                    "exchange_symbol": f"{symbol}/USD",
                    "open": close * 0.99 if close > 0 else close,
                    "high": close * 1.01 if close > 0 else close,
                    "low": close * 0.98 if close > 0 else close,
                    "close": close,
                    "volume": 1000 + i,
                    "source": "coinbase",
                    "snapshot_id": "market-snap",
                    "fetched_at_utc": "2026-04-30T00:00:00+00:00",
                    "is_forward_filled": False,
                    "is_incomplete_dropped": False,
                    "data_type": "exchange_ohlcv",
                    "is_full_ohlcv": True,
                    "quote_currency": "USD",
                }
            )
    market_df = pd.DataFrame(market_rows)
    market_df.to_parquet(root / "raw" / "market" / "market_ohlcv.parquet", index=False)
    with open(root / "raw" / "market" / "market_manifest.json", "w") as fh:
        json.dump(
            {
                "snapshot_id": "market-snap",
                "requested_assets": len(symbols),
                "fetched_assets": len(symbols),
                "full_ohlcv_assets": len(symbols),
            },
            fh,
            indent=2,
        )

    feature_rows = []
    for sym_idx, symbol in enumerate(symbols):
        for i, date_ts in enumerate(dates):
            feature_rows.append(
                {
                    "date_ts": date_ts,
                    "symbol": symbol,
                    "log_ret_1d": sym_idx + i * 0.01,
                    "realized_vol_7d": 0.1 + i * 0.001,
                    "momentum_7_30": sym_idx * 0.2 + i * 0.002,
                    "is_forward_filled_market": False,
                    "onchain_available": int(symbol in {"BTC", "ETH", "SOL", "UNI"}),
                    "feature_set": "full",
                    "feature_version": "full_v1",
                    "snapshot_id": "feature-snap",
                    "run_id": "feat-run",
                    "created_at_utc": "2026-04-30T00:00:00+00:00",
                }
            )
    feature_df = pd.DataFrame(feature_rows)
    if leakage_col:
        feature_df[leakage_col] = 1.0
    feature_df.to_parquet(root / "features" / "full_features.parquet", index=False)
    if include_pruned:
        pruned = feature_df[
            ["date_ts", "symbol", "log_ret_1d", "is_forward_filled_market", "onchain_available", "snapshot_id", "run_id", "created_at_utc"]
        ].copy()
        pruned["feature_set"] = "full"
        pruned["feature_version"] = "full_v1"
        pruned.to_parquet(root / "features" / "full_features_pruned.parquet", index=False)
    with open(root / "features" / "feature_manifest.json", "w") as fh:
        json.dump(
            {
                "snapshot_id": "feature-snap",
                "market_rows": len(feature_df),
                "full_rows": len(feature_df),
                "market_symbols": len(symbols),
                "full_symbols": len(symbols),
                "final_kept_feature_count": 3,
            },
            fh,
            indent=2,
        )


def _run_agent(tmp_path: Path, cfg: dict | None = None) -> LabelAgent:
    cfg = cfg or _cfg(tmp_path)
    agent = LabelAgent(cfg)
    assert agent.execute(max_retries=1)
    return agent


def _read_outputs(tmp_path: Path, out_subdir: str = "data/labels_smoke"):
    out_dir = tmp_path / out_subdir
    labels7 = pd.read_parquet(out_dir / "labels_7d.parquet")
    labels14 = pd.read_parquet(out_dir / "labels_14d.parquet")
    labels30 = pd.read_parquet(out_dir / "labels_30d.parquet")
    matrix = pd.read_parquet(out_dir / "label_matrix.parquet")
    modeling = pd.read_parquet(out_dir / "modeling_dataset.parquet")
    coverage = pd.read_parquet(out_dir / "label_coverage_report.parquet")
    with open(out_dir / "label_manifest.json", "r") as fh:
        manifest = json.load(fh)
    return labels7, labels14, labels30, matrix, modeling, coverage, manifest


def test_label_agent_requires_canonical_market_file(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(FileNotFoundError):
        LabelAgent(cfg).prepare()


def test_label_agent_does_not_use_old_per_symbol_market_pattern(tmp_path):
    cfg = _cfg(tmp_path)
    old_dir = tmp_path / "data" / "raw" / "market"
    old_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"date_ts": "2026-01-01", "symbol": "BTC", "close": 1}]).to_parquet(old_dir / "BTC_ohlcv.parquet", index=False)
    with pytest.raises(FileNotFoundError):
        LabelAgent(cfg).prepare()


def test_label_agent_requires_feature_file(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    (tmp_path / "data" / "features" / "full_features.parquet").unlink()
    with pytest.raises(FileNotFoundError):
        LabelAgent(cfg).prepare()


def test_forward_log_return_formula_7d(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path)
    labels7, _, _, _, _, _, _ = _read_outputs(tmp_path)
    row = labels7[labels7["symbol"] == "BTC"].iloc[0]
    expected = np.log(row["close_t_plus_h"] / row["close_t"])
    assert row["label_fwd_logret"] == pytest.approx(expected)


def test_forward_log_return_formula_14d(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path)
    _, labels14, _, _, _, _, _ = _read_outputs(tmp_path)
    row = labels14[labels14["symbol"] == "BTC"].iloc[0]
    expected = np.log(row["close_t_plus_h"] / row["close_t"])
    assert row["label_fwd_logret"] == pytest.approx(expected)


def test_forward_log_return_formula_30d(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path)
    _, _, labels30, _, _, _, _ = _read_outputs(tmp_path)
    row = labels30[labels30["symbol"] == "BTC"].iloc[0]
    expected = np.log(row["close_t_plus_h"] / row["close_t"])
    assert row["label_fwd_logret"] == pytest.approx(expected)


def test_drops_incomplete_horizon_rows(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], periods=50)
    _run_agent(tmp_path)
    labels7, _, labels30, _, _, coverage, _ = _read_outputs(tmp_path)
    assert len(labels7[labels7["symbol"] == "BTC"]) == 43
    assert len(labels30[labels30["symbol"] == "BTC"]) == 20
    assert int(coverage.loc[coverage["horizon_days"] == 30, "dropped_incomplete_rows"].iloc[0]) > 0


def test_drops_non_exact_calendar_horizon_rows(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], periods=90)
    market_path = tmp_path / "data" / "raw" / "market" / "market_ohlcv.parquet"
    market = pd.read_parquet(market_path)
    drop_date = pd.Timestamp("2026-01-08", tz="UTC")
    market = market[market["date_ts"] != drop_date]
    market.to_parquet(market_path, index=False)
    _run_agent(tmp_path)
    *_rest, coverage, _manifest = _read_outputs(tmp_path)
    assert int(coverage.loc[coverage["horizon_days"] == 7, "dropped_non_exact_horizon_rows"].iloc[0]) > 0


def test_does_not_clip_non_positive_prices(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], bad_close={("BTC", 0): 0.0})
    with pytest.raises(LabelAgentError):
        LabelAgent(cfg).prepare()


def test_fails_on_non_positive_prices(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], bad_close={("ETH", 12): -5.0})
    assert not LabelAgent(cfg).execute(max_retries=1)


def test_label_matrix_written(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path)
    assert (tmp_path / "data" / "labels_smoke" / "label_matrix.parquet").exists()


def test_modeling_dataset_written(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path)
    assert (tmp_path / "data" / "labels_smoke" / "modeling_dataset.parquet").exists()


def test_modeling_dataset_uses_pruned_features_when_configured(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], include_pruned=True)
    _run_agent(tmp_path, cfg)
    _, _, _, _, modeling, _, _ = _read_outputs(tmp_path)
    assert "realized_vol_7d" not in modeling.columns
    assert "log_ret_1d" in modeling.columns


def test_modeling_dataset_can_write_unpruned(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["labels"]["also_write_unpruned_modeling_dataset"] = True
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], include_pruned=True)
    _run_agent(tmp_path, cfg)
    unpruned = pd.read_parquet(tmp_path / "data" / "labels_smoke" / "modeling_dataset_unpruned.parquet")
    assert "realized_vol_7d" in unpruned.columns


def test_no_duplicate_symbol_date(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path)
    _, _, _, matrix, modeling, _, _ = _read_outputs(tmp_path)
    assert not matrix.duplicated(["symbol", "date_ts"]).any()
    assert not modeling.duplicated(["symbol", "date_ts"]).any()


def test_no_null_labels(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path)
    labels7, labels14, labels30, _, _, _, _ = _read_outputs(tmp_path)
    for df in [labels7, labels14, labels30]:
        assert df["label_fwd_logret"].notna().all()


def test_no_infinite_labels(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path)
    labels7, labels14, labels30, _, _, _, _ = _read_outputs(tmp_path)
    for df in [labels7, labels14, labels30]:
        assert np.isfinite(df["label_fwd_logret"]).all()


def test_future_date_after_label_date(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path)
    labels7, _, _, _, _, _, _ = _read_outputs(tmp_path)
    assert (pd.to_datetime(labels7["future_date_ts"], utc=True) > pd.to_datetime(labels7["date_ts"], utc=True)).all()


def test_no_label_columns_in_feature_inputs(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], leakage_col="label_bad")
    assert not LabelAgent(cfg).execute(max_retries=1)


def test_verify_label_run_passes_valid_fixture(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL", "UNI", "AAVE"])
    _run_agent(tmp_path, cfg)
    assert validate_label_outputs(cfg) == []


def test_verify_label_run_rejects_duplicate_symbol_date(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path, cfg)
    path = tmp_path / "data" / "labels_smoke" / "labels_7d.parquet"
    df = pd.read_parquet(path)
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    df.to_parquet(path, index=False)
    failures = validate_label_outputs(cfg)
    assert any("duplicate symbol + date_ts" in f for f in failures)


def test_verify_label_run_rejects_null_labels(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path, cfg)
    path = tmp_path / "data" / "labels_smoke" / "labels_14d.parquet"
    df = pd.read_parquet(path)
    df.loc[df.index[0], "label_fwd_logret"] = np.nan
    df.to_parquet(path, index=False)
    failures = validate_label_outputs(cfg)
    assert any("null/non-numeric label_fwd_logret" in f for f in failures)


def test_verify_label_run_rejects_formula_mismatch(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path, cfg)
    path = tmp_path / "data" / "labels_smoke" / "labels_30d.parquet"
    df = pd.read_parquet(path)
    df.loc[df.index[0], "label_fwd_logret"] = 999
    df.to_parquet(path, index=False)
    failures = validate_label_outputs(cfg)
    assert any("formula mismatch" in f for f in failures)


def test_verify_label_run_rejects_feature_leakage_columns(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path, cfg)
    feature_path = tmp_path / "data" / "features" / "full_features.parquet"
    df = pd.read_parquet(feature_path)
    df["future_signal"] = 1.0
    df.to_parquet(feature_path, index=False)
    failures = validate_label_outputs(cfg)
    assert any("prohibited leakage columns" in f for f in failures)


def test_manifest_counts_match_outputs(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL", "UNI"])
    _run_agent(tmp_path)
    labels7, labels14, labels30, matrix, modeling, _, manifest = _read_outputs(tmp_path)
    assert manifest["label_rows_by_horizon"] == {"7": len(labels7), "14": len(labels14), "30": len(labels30)}
    assert manifest["label_matrix_rows"] == len(matrix)
    assert manifest["modeling_dataset_rows"] == len(modeling)


def test_coverage_report_counts_match_outputs(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL", "UNI"])
    _run_agent(tmp_path)
    labels7, labels14, labels30, _, _, coverage, _ = _read_outputs(tmp_path)
    actual = {7: len(labels7), 14: len(labels14), 30: len(labels30)}
    for _, row in coverage.iterrows():
        assert row["valid_label_rows"] == actual[int(row["horizon_days"])]


def test_recommended_embargo_equals_max_horizon(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path)
    _, _, _, _, _, _, manifest = _read_outputs(tmp_path)
    assert manifest["recommended_embargo_days"] == 30


def test_label_rank_pct_by_date(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL", "UNI", "AAVE"])
    _run_agent(tmp_path)
    labels7, _, _, _, _, _, _ = _read_outputs(tmp_path)
    sample_date = labels7["date_ts"].min()
    grp = labels7[labels7["date_ts"] == sample_date]
    assert grp["label_rank_pct"].between(0, 1).all()


def test_quantile_buckets_by_date(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL", "UNI", "AAVE"])
    _run_agent(tmp_path)
    labels7, _, _, _, _, _, _ = _read_outputs(tmp_path)
    assert labels7["label_quantile_bucket"].dropna().between(1, 5).all()


def test_modeling_dataset_inner_join_features_labels(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path)
    _, _, _, matrix, modeling, _, _ = _read_outputs(tmp_path)
    assert len(modeling) == len(matrix)


def test_no_labels_written_to_data_features_dir(tmp_path):
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"])
    _run_agent(tmp_path)
    feature_files = list((tmp_path / "data" / "features").glob("*.parquet"))
    assert not any("label" in p.name for p in feature_files)
