#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from configs.config import load_config  # noqa: E402


CMC_BASE = "https://pro-api.coinmarketcap.com"
READINESS_DIR = PROJECT_ROOT / "data" / "readiness"
DOC_PATH = PROJECT_ROOT / "docs" / "API_DATA_READINESS_AUDIT.md"


def _masked(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {"present": False, "masked": None}
    suffix = value[-4:] if len(value) >= 4 else "****"
    return {"present": True, "masked": f"***{suffix}", "length": len(value)}


def _key_status() -> Dict[str, Any]:
    keys = {
        "CMC_API_KEY": os.getenv("CMC_API_KEY"),
        "COINMARKETCAP_API_KEY": os.getenv("COINMARKETCAP_API_KEY"),
        "ETHERSCAN_API_KEY": os.getenv("ETHERSCAN_API_KEY"),
        "DUNE_API_KEY": os.getenv("DUNE_API_KEY"),
        "THEGRAPH_API_KEY": os.getenv("THEGRAPH_API_KEY"),
        "GRAPH_API_KEY": os.getenv("GRAPH_API_KEY"),
        "COINGECKO_API_KEY": os.getenv("COINGECKO_API_KEY"),
        "COINMETRICS_API_KEY": os.getenv("COINMETRICS_API_KEY"),
        "DEFILLAMA_API_KEY": os.getenv("DEFILLAMA_API_KEY"),
    }
    return {name: _masked(value) for name, value in keys.items()}


def _safe_get(
    name: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    safe_params = dict(params or {})
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        body: Any
        try:
            body = resp.json()
        except Exception:
            body = {"text": resp.text[:300]}
        status = body.get("status", {}) if isinstance(body, dict) else {}
        if not isinstance(status, dict):
            status = {}
        data = body.get("data") if isinstance(body, dict) else None
        err = status.get("error_message")
        if err is None and isinstance(body, dict):
            msg = body.get("message")
            err = msg if isinstance(msg, str) else None
        if err is None and isinstance(data, str):
            err = data[:300]
        result_payload = body.get("result") if isinstance(body, dict) else None
        if err is None and isinstance(result_payload, str):
            err = result_payload[:300]
        return {
            "name": name,
            "url": url,
            "params": safe_params,
            "http_status": resp.status_code,
            "ok": 200 <= resp.status_code < 300,
            "cmc_error_code": status.get("error_code") if isinstance(status, dict) else None,
            "error_message": err,
            "sample_count": _sample_count(body),
        }
    except Exception as exc:
        return {
            "name": name,
            "url": url,
            "params": safe_params,
            "http_status": None,
            "ok": False,
            "error_message": str(exc),
            "sample_count": 0,
        }


def _sample_count(body: Any) -> int:
    if not isinstance(body, dict):
        return 0
    data = body.get("data")
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        if isinstance(data.get("quotes"), list):
            return len(data["quotes"])
        return len(data)
    result = body.get("result")
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        return len(result)
    return 0


def _probe_cmc() -> Dict[str, Any]:
    key = os.getenv("CMC_API_KEY") or os.getenv("COINMARKETCAP_API_KEY")
    result: Dict[str, Any] = {
        "provider": "coinmarketcap",
        "key_present": bool(key),
        "cmc_key_visible": bool(key),
        "listings_historical_works": False,
        "cmc_listings_historical_recent_window_works": False,
        "cmc_listings_historical_3y_works": False,
        "cmc_listings_historical_access_window_observed": "unknown",
        "cmc_quotes_historical_access_window_observed": "unknown",
        "cmc_ohlcv_supported": False,
        "professor_historical_universe_ready": False,
        "recommended_universe_mode": "latest_survivor_baseline_until_cmc_upgrade",
        "accessible_date_range_observed": [],
        "probes": [],
    }
    if not key:
        result["error_message"] = "CMC_API_KEY/COINMARKETCAP_API_KEY missing; live CMC probes skipped"
        result["cmc_listings_historical_access_window_observed"] = "not tested: key missing"
        result["cmc_quotes_historical_access_window_observed"] = "not tested: key missing"
        return result
    headers = {"X-CMC_PRO_API_KEY": key}
    recent_date = (datetime.now(timezone.utc) - timedelta(days=14)).date().isoformat()
    recent_params = {
        "date": recent_date,
        "start": 1,
        "limit": 10,
        "convert": "USD",
        "sort": "cmc_rank",
        "sort_dir": "desc",
        "cryptocurrency_type": "all",
    }
    recent_probe = _safe_get(
        f"cmc_listings_historical_recent_{recent_date}",
        f"{CMC_BASE}/v1/cryptocurrency/listings/historical",
        params=recent_params,
        headers=headers,
    )
    result["probes"].append(recent_probe)
    result["cmc_listings_historical_recent_window_works"] = bool(recent_probe["ok"] and int(recent_probe.get("sample_count") or 0) > 0)
    for date in ["2023-05-01", "2024-05-01", "2026-03-31"]:
        params = {
            "date": date,
            "start": 1,
            "limit": 10,
            "convert": "USD",
            "sort": "cmc_rank",
            "sort_dir": "desc",
            "cryptocurrency_type": "all",
        }
        probe = _safe_get(
            f"cmc_listings_historical_{date}",
            f"{CMC_BASE}/v1/cryptocurrency/listings/historical",
            params=params,
            headers=headers,
        )
        result["probes"].append(probe)
        if probe["ok"] and int(probe.get("sample_count") or 0) > 0:
            result["accessible_date_range_observed"].append(date)
    result["listings_historical_works"] = bool(result["accessible_date_range_observed"])
    result["cmc_listings_historical_3y_works"] = False
    quotes_params = {
        "id": 1,
        "time_start": "2023-05-01",
        "time_end": "2023-05-10",
        "interval": "daily",
        "convert": "USD",
    }
    quotes_probe = _safe_get(
            "cmc_quotes_historical_btc",
            f"{CMC_BASE}/v2/cryptocurrency/quotes/historical",
            params=quotes_params,
            headers=headers,
    )
    result["probes"].append(quotes_probe)
    ohlcv_params = dict(quotes_params)
    ohlcv_probe = _safe_get(
            "cmc_ohlcv_historical_btc_access_check",
            f"{CMC_BASE}/v2/cryptocurrency/ohlcv/historical",
            params=ohlcv_params,
            headers=headers,
    )
    result["probes"].append(ohlcv_probe)
    all_errors = " ".join(str(p.get("error_message") or "") for p in result["probes"])
    if "1 months of historical access" in all_errors or "1 month" in all_errors:
        result["cmc_listings_historical_access_window_observed"] = "1 month"
    if "12 months of historical access" in all_errors or "12 month" in all_errors:
        result["cmc_quotes_historical_access_window_observed"] = "12 months"
    result["cmc_ohlcv_supported"] = bool(ohlcv_probe["ok"] and int(ohlcv_probe.get("sample_count") or 0) > 0)
    result["professor_historical_universe_ready"] = False
    failed = [p for p in result["probes"] if not p["ok"]]
    if failed and not result["listings_historical_works"]:
        result["error_message"] = "; ".join(str(p.get("error_message") or p.get("http_status")) for p in failed[:3])
    return result


def _probe_keyless() -> Dict[str, Any]:
    return {
        "coinmetrics": _safe_get(
            "coinmetrics_catalog_assets",
            "https://community-api.coinmetrics.io/v4/catalog/assets",
            params={"assets": "btc"},
        ),
        "defillama": _safe_get("defillama_chains", "https://api.llama.fi/v2/chains"),
    }


def _probe_optional(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    etherscan_key = os.getenv("ETHERSCAN_API_KEY")
    if etherscan_key:
        out["etherscan"] = _safe_get(
            "etherscan_v2_eth_supply",
            "https://api.etherscan.io/v2/api",
            params={"chainid": 1, "module": "stats", "action": "ethsupply", "apikey": etherscan_key},
        )
        out["etherscan"]["params"]["apikey"] = "***masked***"
    else:
        out["etherscan"] = {"ok": False, "skipped": True, "reason": "ETHERSCAN_API_KEY missing"}
    graph_key = os.getenv("THEGRAPH_API_KEY") or os.getenv("GRAPH_API_KEY")
    subgraphs = (((cfg.get("onchain") or {}).get("thegraph") or {}).get("configured_subgraphs") or {})
    out["thegraph"] = {
        "ok": False,
        "skipped": True,
        "reason": "GRAPH/THEGRAPH key or configured subgraph missing" if not (graph_key and subgraphs) else "probe not implemented without configured endpoint",
        "key_present": bool(graph_key),
        "configured_subgraphs": sorted(subgraphs.keys()) if isinstance(subgraphs, dict) else [],
    }
    dune_key = os.getenv("DUNE_API_KEY")
    query_ids = (((cfg.get("onchain") or {}).get("dune") or {}).get("query_ids") or {})
    out["dune"] = {
        "ok": False,
        "skipped": True,
        "reason": "DUNE_API_KEY or configured query_ids missing" if not (dune_key and query_ids) else "probe skipped to avoid executing paid/large query",
        "key_present": bool(dune_key),
        "configured_query_ids": sorted(query_ids.keys()) if isinstance(query_ids, dict) else [],
    }
    return out


def _upsert_doc_section(title: str, body: str) -> None:
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    start = f"<!-- {title}:START -->"
    end = f"<!-- {title}:END -->"
    existing = DOC_PATH.read_text() if DOC_PATH.exists() else "# API/Data Readiness Audit\n\n"
    section = f"{start}\n{body.rstrip()}\n{end}\n"
    if start in existing and end in existing:
        before = existing.split(start)[0]
        after = existing.split(end, 1)[1]
        DOC_PATH.write_text(before + section + after.lstrip("\n"))
    else:
        DOC_PATH.write_text(existing.rstrip() + "\n\n" + section)


def _markdown(results: Dict[str, Any]) -> str:
    cmc = results["providers"]["coinmarketcap"]
    lines = [
        "## API Readiness",
        "",
        f"- Created at UTC: `{results['created_at_utc']}`",
        f"- Config: `{results['config_path']}`",
        "- Secret handling: API keys are reported only as present/missing/masked; full secrets are never printed.",
        "",
        "### Key Status",
    ]
    for key, status in results["key_status"].items():
        lines.append(f"- `{key}`: {'present ' + status['masked'] if status['present'] else 'missing'}")
    lines.extend(
        [
            "",
            "### CoinMarketCap",
            f"- CMC key visible: `{str(cmc.get('cmc_key_visible', cmc.get('key_present'))).lower()}`",
            f"- Recent-window `/v1/cryptocurrency/listings/historical` works: `{str(cmc.get('cmc_listings_historical_recent_window_works')).lower()}`",
            f"- 3-year `/v1/cryptocurrency/listings/historical` works: `{str(cmc.get('cmc_listings_historical_3y_works')).lower()}`",
            f"- Observed listings/historical access window: `{cmc.get('cmc_listings_historical_access_window_observed')}`",
            f"- Observed quotes/historical access window: `{cmc.get('cmc_quotes_historical_access_window_observed')}`",
            f"- CMC OHLCV historical supported: `{str(cmc.get('cmc_ohlcv_supported')).lower()}`",
            f"- Professor-grade 3-year point-in-time universe ready: `{str(cmc.get('professor_historical_universe_ready')).lower()}`",
            f"- Recommended universe mode: `{cmc.get('recommended_universe_mode')}`",
            f"- `accessible_date_range_observed`: `{cmc.get('accessible_date_range_observed', [])}`",
            f"- Error/limitation: `{cmc.get('error_message') or ''}`",
            "- Decision: do not proceed to CMC 3-year historical universe construction; proceed next with the latest-survivor/free-provider baseline and explicit survivorship-bias disclosure.",
            "",
            "### Provider Probe Summary",
        ]
    )
    for provider, payload in results["providers"].items():
        if provider == "coinmarketcap":
            ok = payload.get("listings_historical_works")
            lines.append(f"- `{provider}`: historical listings {'accessible' if ok else 'not confirmed'}")
        elif isinstance(payload, dict) and "ok" in payload:
            lines.append(f"- `{provider}`: ok=`{payload.get('ok')}`, status=`{payload.get('http_status')}`, reason=`{payload.get('error_message') or payload.get('reason') or ''}`")
        else:
            lines.append(f"- `{provider}`: recorded")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/run_config.yaml")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    results = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str(Path(args.config)),
        "key_status": _key_status(),
        "providers": {},
    }
    results["providers"]["coinmarketcap"] = _probe_cmc()
    results["providers"].update(_probe_keyless())
    results["providers"].update(_probe_optional(cfg))
    cmc = results["providers"]["coinmarketcap"]
    results["cmc_key_visible"] = bool(cmc.get("cmc_key_visible"))
    results["cmc_listings_historical_recent_window_works"] = bool(cmc.get("cmc_listings_historical_recent_window_works"))
    results["cmc_listings_historical_3y_works"] = False
    results["cmc_listings_historical_access_window_observed"] = cmc.get("cmc_listings_historical_access_window_observed")
    results["cmc_quotes_historical_access_window_observed"] = cmc.get("cmc_quotes_historical_access_window_observed")
    results["cmc_ohlcv_supported"] = bool(cmc.get("cmc_ohlcv_supported"))
    results["professor_historical_universe_ready"] = False
    results["recommended_universe_mode"] = "latest_survivor_baseline_until_cmc_upgrade"
    READINESS_DIR.mkdir(parents=True, exist_ok=True)
    (READINESS_DIR / "api_probe_results.json").write_text(json.dumps(results, indent=2, default=str))
    _upsert_doc_section("API_READINESS", _markdown(results))
    print(
        "API probe complete. "
        f"CMC recent listings works={cmc.get('cmc_listings_historical_recent_window_works')}; "
        "CMC 3-year historical universe ready=False. "
        f"Report: {DOC_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
