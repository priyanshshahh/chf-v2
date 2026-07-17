#!/usr/bin/env python3
"""dex_reference.py — DEX platform / network reference table.

Endpoint: /v1/dex/platform/list. A static reference of the on-chain networks CMC
tracks (id, name, chain id, native token address, block-explorer URL templates,
linked CMC crypto id). Keyed on ``platform_id``.

SCOPE: reference only. Token-level / pair-level / OHLCV DEX data is OUT OF SCOPE
for this research (those endpoints require key entitlements / return 5xx on this
plan). Only the platform reference table is collected here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from .base import finalize, save_raw_sample

DATASET = "pro_api_dex_reference"
FILENAME = "cmc_dex_platforms.parquet"
SOURCE = "pro_api:/v1/dex/platform/list"
KEY = ["platform_id"]
GENERATED_BY = "src/cmc/collectors/dex_reference.py"


def _parse(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    recs = []
    for r in rows:
        recs.append(
            {
                "platform_id": r.get("id"),
                "name": r.get("n"),
                "short_name": r.get("dn"),
                "platform_alias": r.get("pltA"),
                "chain_id": r.get("chId"),
                "crypto_id": r.get("cid"),
                "native_token_address": r.get("na"),
                "token_explorer_url_format": r.get("uf"),
                "tx_explorer_url_format": r.get("txuf"),
                "address_explorer_url_format": r.get("addrUrl"),
                "is_verified": r.get("v"),
            }
        )
    return recs


def run(client, base_dir: Path, start=None, end=None) -> dict:
    out_dir = Path(base_dir) / DATASET
    payload = client.get("/v1/dex/platform/list")
    rows = payload.get("data") or []
    save_raw_sample(out_dir, "dex_platforms_sample.json",
                    {"status": {"error_code": 0}, "data": rows[:5]})

    df = pd.DataFrame(_parse(rows))
    df["platform_id"] = pd.to_numeric(df["platform_id"], errors="coerce").astype("Int64")
    df["chain_id"] = pd.to_numeric(df["chain_id"], errors="coerce").astype("Int64")
    df["crypto_id"] = pd.to_numeric(df["crypto_id"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["platform_id"]).drop_duplicates(subset=["platform_id"])

    return finalize(
        df, out_dir=out_dir, filename=FILENAME, dataset=DATASET, source=SOURCE,
        key=KEY, generated_by=GENERATED_BY, date_col=None, symbol_col="name",
        run={"note": ("Platform/network reference only. Token/pair/OHLCV DEX data "
                      "is OUT OF SCOPE for this research (needs entitlements / 5xx)."),
             "platform_count": len(df)},
    )
