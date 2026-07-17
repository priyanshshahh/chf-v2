#!/usr/bin/env python3
"""
build_cmc_docs.py — Generate cmc_complete/cmc_docs.docx (full documentation of everything
extracted from the CoinMarketCap API) and sync cmc_complete/ — the single canonical folder
for all CMC data, code copies, and docs.

cmc_complete/data/ is the permanent home for every CMC dataset (collectors write there
directly via src/cmc/maintain.py's BASE_DIR) -- this script does NOT copy or wipe data/.
It only (re)writes the docx and syncs code+docs copies from their live source locations.

Run:  .venv/bin/python scripts/build_cmc_docs.py
"""
from pathlib import Path
import shutil
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "cmc_complete"
OUT_DOCX = BUNDLE / "cmc_docs.docx"

# ----------------------------------------------------------------------------- catalog
DATASETS = [
    dict(name="Full daily quotes (ALL coins)", star=True,
         file="coinmarketcap_data/pro_api_quotes_historical/cmc_quotes_daily_full.parquet",
         endpoint="/v3/cryptocurrency/quotes/historical [Q&A: daily-end quotes only "
                  "(interval=daily param); NOT minute-to-minute -- no intraday/minute-level "
                  "CMC data exists anywhere in this project]", rows="5,898,202",
         coverage="2023-07-03 to 2026-07-01 (36-month cap)",
         entities="8,133 coins (incl. delisted-in-window)",
         cols="date, cmc_id, symbol, name, price, market_cap, volume_24h",
         status="COLLECTED - core research asset",
         note="Daily price / market cap / 24h volume in USD for the full active universe plus "
              "coins delisted within the window. Keyed on (cmc_id, date). Expands the earlier "
              "~1,200-symbol set to 8,133 coins."),
    dict(name="Coin ID map (directory)", star=False,
         file="coinmarketcap_data/pro_api_map/cmc_map.parquet",
         endpoint="/v1/cryptocurrency/map", rows="37,127",
         coverage="historical span 2010-07-13 to 2026-06-30",
         entities="8,132 active / 28,995 inactive; 25,117 symbols",
         cols="cmc_id, symbol, name, slug, is_active, first_historical_data, last_historical_data, source",
         status="COLLECTED - survivorship-free",
         note="Directory of every coin CMC has ever listed (active + inactive + untracked). "
              "is_active flags delisted coins (e.g. Terra Classic / LUNC). cmc_id is the join key."),
    dict(name="Keyless historical listings", star=False,
         file="coinmarketcap_data/keyless_data_api_listings/cmc_web_listings_historical.parquet",
         endpoint="data-api/v3/cryptocurrency/listings/historical (keyless web source)", rows="19,800",
         coverage="monthly 2021-01-01 to 2026-06-01 (top 300) [Q&A: confirmed MONTHLY, not "
                  "daily -- 66 snapshots; a separate DAILY extraction of the same keyless source "
                  "also exists, in data/keyless_daily_extraction/, covering 2023-06-19 to "
                  "2026-06-17]",
         entities="1,200 symbols",
         cols="snapshot_date, cmc_id, rank, symbol, name, slug, market_cap_usd, price_usd, "
              "volume_24h_usd, circulating_supply, total_supply, max_supply, num_market_pairs, "
              "date_added, raw_category_tags, source",
         status="COLLECTED - survivorship-free (no API key)",
         note="Point-in-time ranked listings incl. delisted coins, obtained without an API key. "
              "[Q&A: no wallet/address counts are included -- CMC doesn't provide this; the "
              "project's only address-count data (CoinMetrics AdrActCnt) is unrelated to CMC.]"),
    dict(name="Original curated quotes", star=False,
         file="coinmarketcap_data/pro_api_quotes_historical/cmc_quotes_history.parquet "
              "(+ cmc_prices_history.parquet)",
         endpoint="/v3/cryptocurrency/quotes/historical (earlier pull) [Q&A: same v3 endpoint "
                  "as the full-universe pull above -- previously mislabeled v2, now corrected. "
                  "v2 vs v3: both are live CMC endpoints; the only genuine v2 usage in this "
                  "project is the original access-probe script (testing only), while all "
                  "production quotes collection uses v3 for its multi-coin batching efficiency]",
         rows="225,854 each",
         coverage="~36 months", entities="~1,200 symbols",
         cols="date, symbol, name, market_cap, price, volume_24h (+ close/volume in prices file)",
         status="RETAINED (superseded by cmc_quotes_daily_full)",
         note="The earlier curated quotes files, kept for continuity/reference."),
    dict(name="Global market metrics", star=False,
         file="coinmarketcap_data/pro_api_global_metrics_historical/cmc_global_metrics.parquet",
         endpoint="/v1/global-metrics/quotes/historical", rows="1,096",
         coverage="2023-07-02 to 2026-07-01 (36-month cap)", entities="daily whole-market",
         cols="date, total_market_cap, total_volume_24h, altcoin_market_cap, btc_dominance, "
              "eth_dominance, active_cryptocurrencies",
         status="COLLECTED (history capped at 36 months by plan)",
         note="Whole-market daily aggregates in USD: total market cap, volume, BTC/ETH dominance."),
    dict(name="CMC 100 Index", star=True,
         file="coinmarketcap_data/pro_api_index_historical/cmc100_index.parquet",
         endpoint="/v3/index/cmc100-historical", rows="913",
         coverage="2024-01-01 to 2026-07-01 (plan floor 2024-01-01); latest 114.67",
         entities="daily index", cols="date, value, num_constituents, constituents_json",
         status="COLLECTED - the advisor's 'the 100'",
         note="CoinMarketCap 100 Index daily values with constituent breakdown."),
    dict(name="CMC 20 Index", star=True,
         file="coinmarketcap_data/pro_api_index_historical/cmc20_index.parquet",
         endpoint="/v3/index/cmc20-historical", rows="913",
         coverage="2024-01-01 to 2026-07-01 (plan floor 2024-01-01); latest 119.56",
         entities="daily index", cols="date, value, num_constituents, constituents_json",
         status="COLLECTED - the advisor's 'the 20'",
         note="CoinMarketCap 20 Index daily values with constituent breakdown."),
    dict(name="Fear & Greed index", star=False,
         file="coinmarketcap_data/pro_api_fear_greed/cmc_fear_greed.parquet",
         endpoint="/v3/fear-and-greed/historical", rows="1,098",
         coverage="2023-06-29 to 2026-06-30", entities="daily sentiment",
         cols="date, value, value_classification",
         status="COLLECTED", note="CMC Crypto Fear & Greed sentiment index, one value/day. "
              "Pre-split data may still live in the legacy combined pro_api_index_historical/ dir."),
    dict(name="Altcoin Season index", star=False,
         file="coinmarketcap_data/pro_api_altcoin_season/cmc_altcoin_season.parquet",
         endpoint="/v1/altcoin-season-index/historical", rows="90",
         coverage="2026-04-03 to 2026-07-01 (endpoint exposes only 90 days)",
         entities="daily index", cols="date, altcoin_index, altcoin_marketcap",
         status="COLLECTED (90-day max is a CMC limit)",
         note="Altcoin Season Index. CMC publishes only a rolling 90-day window for this index. "
              "Pre-split data may still live in the legacy combined pro_api_index_historical/ dir."),
    dict(name="Exchange directory", star=False,
         file="coinmarketcap_data/pro_api_exchange_map/cmc_exchange_map.parquet",
         endpoint="/v1/exchange/map", rows="1,257",
         coverage="historical span 2018-04-26 to 2026-07-01 [Q&A: yes, one row per exchange "
                  "(1,257 total) -- this is a directory, not a time series; the span is the "
                  "earliest/latest first/last_historical_data across all exchanges]",
         entities="active + inactive exchanges",
         cols="exchange_id, name, slug, is_active, first_historical_data, last_historical_data, source",
         status="COLLECTED", note="Directory of crypto exchanges (active and inactive)."),
    dict(name="Airdrops", star=False,
         file="coinmarketcap_data/pro_api_airdrops/cmc_airdrops.parquet",
         endpoint="/v1/cryptocurrency/airdrops", rows="426",
         coverage="2021-05-01 to 2022-11-25 (ENDED 420 / ONGOING 5 / UPCOMING 1)",
         entities="363 coins",
         cols="airdrop_id, project_name, status, coin_id, coin_symbol, coin_name, start_date, "
              "end_date, total_prize, winner_count, link",
         status="COLLECTED", note="Airdrop events across all statuses."),
    dict(name="Fiat / metals reference", star=False,
         file="coinmarketcap_data/pro_api_fiat_map/cmc_fiat_map.parquet",
         endpoint="/v1/fiat/map", rows="97", coverage="reference (no dates)",
         entities="world currencies + metals", cols="fiat_id, symbol, name, sign, source",
         status="COLLECTED", note="Reference list of fiat currencies and metals for USD conversion."),
    dict(name="Fiat to USD snapshot", star=False,
         file="coinmarketcap_data/pro_api_fiat_fx/cmc_fiat_usd_snapshot.parquet",
         endpoint="/v2/tools/price-conversion", rows="88",
         coverage="CURRENT snapshot 2026-07-01", entities="88 of 97 fiats/metals",
         cols="snapshot_date, fiat_id, fiat_symbol, usd_to_fiat_rate, source",
         status="COLLECTED - snapshot only",
         note="Current USD->fiat conversion rates. LIMITATION: this plan has no bulk historical FX "
              "time-series; historical conversion needs one call per date. Full historical FX is a gap."),
    dict(name="DEX platform reference", star=False,
         file="coinmarketcap_data/pro_api_dex_reference/cmc_dex_platforms.parquet",
         endpoint="/v1/dex/platform/list", rows="116", coverage="reference",
         entities="blockchain networks/platforms",
         cols="platform_id, name, short_name, platform_alias, chain_id, crypto_id, "
              "native_token_address, token_explorer_url_format, tx_explorer_url_format, "
              "address_explorer_url_format, is_verified",
         status="COLLECTED - reference only",
         note="Blockchain platform/network reference. Token-level on-chain DEX data is out of scope."),
    dict(name="Pro listings sample", star=False,
         file="coinmarketcap_data/pro_api_listings_historical/cmc_listings_historical.parquet",
         endpoint="/v1/cryptocurrency/listings/historical (Pro) [Q&A: little that's unique -- "
                  "a single-date (2026-06-01) proof sample capped at 1 month by the Hobbyist "
                  "plan; the keyless listings/historical source above supersedes it for real "
                  "historical coverage]", rows="100",
         coverage="single-date sample", entities="top 100 one snapshot",
         cols="cmc_id, provider_asset_id, coin_id, symbol, name, slug, market_cap_rank, "
              "market_cap_usd, volume_24h_usd, price_usd, is_active_at_snapshot, raw_category_tags, "
              "source, snapshot_date",
         status="SAMPLE (Pro listings/historical is 1-month capped on this plan)",
         note="Small reference sample; the keyless source above supersedes it for long history."),
    dict(name="OHLCV sample (placeholder)", star=False,
         file="coinmarketcap_data/pro_api_ohlcv_historical/cmc_ohlcv_sample.parquet",
         endpoint="/v2/cryptocurrency/ohlcv/historical", rows="0",
         coverage="n/a", entities="n/a",
         cols="cmc_id, date, symbol, open, high, low, close, volume, market_cap",
         status="BLOCKED (HTTP 403 - not on this plan)",
         note="Daily OHLCV is NOT available on the current plan. Empty placeholder; requires "
              "upgrade. [Q&A: correct, this dataset contains true daily transaction OHLCV "
              "(open/high/low/close/volume), not just quotes. 24-hour days are UTC-normalized "
              "(00:00-23:59 UTC) per providers/coinmarketcap.py's timestamp handling. Once "
              "unblocked, both active and inactive/delisted coins will be included -- the "
              "sibling quotes/historical collector already builds its universe as active + "
              "delisted-in-window coins, and the OHLCV collector is scaffolded to reuse the "
              "same approach; only a 2-coin BTC/ETH proof pull has run so far.]"),
]

BLOCKED = [
    ("OHLCV historical & latest", "/v2/cryptocurrency/ohlcv/*", "403 - plan does not include it"),
    ("Price performance stats", "/v2/cryptocurrency/price-performance-stats/latest", "403"),
    ("Exchange listings latest", "/v1/exchange/listings/latest", "403"),
    ("Market pairs (crypto & exchange)", "/v2/cryptocurrency/market-pairs, /v1/exchange/market-pairs", "403"),
    ("Trending (latest / gainers-losers / most-visited)", "/v1/cryptocurrency/trending/*", "403"),
    ("Listings new", "/v1/cryptocurrency/listings/new", "403"),
    ("Content & Community", "/v1/content/*, /v1/community/*", "403"),
    ("Derivatives", "/v1/derivatives/*", "404 - not on this plan"),
    ("On-chain DEX token/pair/OHLCV/holders", "/v4/dex/*, /v1/dex/token*, /v1/k-line/*", "key-gated / out of scope"),
    ("Historical depth > 36 months", "all time-series endpoints", "capped at 36 months on this plan"),
]

# Paths are relative to the repo root; each is mirrored into cmc_complete/code/ on every
# run of this script. The keyless extractor lives ONLY inside cmc_complete/code/ now
# (its old standalone location, coinmarketcap_extract/, was consolidated away).
CODE_FILES = [
    ("src/cmc/client.py", "HTTP client: env-based auth, rate-limit pacing, 429/5xx backoff"),
    ("src/cmc/manifest.py", "Read/write per-dataset manifest.json (source, coverage, counts, run meta)"),
    ("src/cmc/storage.py", "Atomic idempotent upsert(df, key, path) - dedup + temp-file + os.replace"),
    ("src/cmc/maintain.py", "Monthly maintenance orchestrator; CLI: python -m src.cmc.maintain; "
                            "writes to cmc_complete/data/coinmarketcap_data/"),
    ("src/cmc/collectors/", "One module per endpoint: map, quotes, ohlcv, global_metrics, fear_greed,"
                            " altcoin_season, cmc_index, exchange_map, exchange_info, fiat_map, fiat_fx,"
                            " categories, info, airdrops, dex_reference (+ base.py helpers)"),
    ("tests/cmc/", "pytest: idempotent storage, manifest round-trip, map collector (delisted retained)"),
    ("providers/coinmarketcap.py", "Original Pro API client (CoinMarketCapProvider)"),
    ("agents/universe_agent_cmc.py", "Back-compat universe-agent shim -> source: cmc_listings_download"),
    ("scripts/build_cmc_web_history.py", "Keyless data-API ingester (production PIT universe source)"),
    ("scripts/build_cmc_quotes_history.py", "Pro quotes/historical ingester"),
    ("scripts/build_cmc_history.py", "Pro listings/historical (+ optional OHLCV) ingester"),
    ("scripts/build_cmc_docs.py", "This script - generates cmc_docs.docx and syncs cmc_complete/"),
    ("cmc_complete/code/keyless_extractor/extract_cmc_daily_history.py",
     "Keyless survivorship-free daily listings extractor (lives only inside cmc_complete now)"),
]


def H(doc, text, level):
    return doc.add_heading(text, level=level)


def build_docx():
    BUNDLE.mkdir(parents=True, exist_ok=True)
    doc = Document()
    style = doc.styles["Normal"]; style.font.name = "Calibri"; style.font.size = Pt(11)

    doc.add_heading("CoinMarketCap Data Extraction - Master Documentation", 0)
    sub = doc.add_paragraph("cmc_docs  -  Generated 2026-07-01  -  CHF research project")
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER

    H(doc, "1. Overview", 1)
    doc.add_paragraph(
        "This document records everything extracted from the CoinMarketCap (CMC) API for the "
        "cryptocurrency research project: where each dataset is stored, its columns, coverage, and "
        "which CMC endpoint produced it. It also documents the plan limits, what could NOT be "
        "collected and why, the extraction code, and how to open the data.")
    doc.add_paragraph(
        "Headline: 17 datasets totalling ~6.41 million rows were collected, including a full daily "
        "price/market-cap/volume history for 8,133 coins (survivorship-free) and the CMC 100 and "
        "CMC 20 indices. All monetary values are in USD; all dates use YYYY-MM-DD. Everything -- "
        "data, code copies, and docs -- lives in one folder: cmc_complete/.")

    H(doc, "2. API access & plan", 1)
    for line in [
        "Base URL: https://pro-api.coinmarketcap.com",
        "Auth: header X-CMC_PRO_API_KEY (stored only in a git-ignored .env as CMC_API_KEY).",
        "Plan: 150,000 credits / month tier. Credits used this extraction: ~62,176.",
        "Historical depth on this plan: 36 months for time-series endpoints.",
        "Keyless option: many reference/index endpoints work with no key via the /public-api/ prefix.",
    ]:
        doc.add_paragraph(line, style="List Bullet")

    H(doc, "3. Advisor requirements -> what was delivered", 1)
    reqs = [
        ("Historical listings incl. delisted coins (no survivorship bias)",
         "cmc_map + keyless historical listings", "DELIVERED"),
        ("Daily price / market cap / volume history", "cmc_quotes_daily_full (8,133 coins, 36 mo)", "DELIVERED"),
        ("Coin 'map'", "cmc_map (/v1/cryptocurrency/map)", "DELIVERED"),
        ("USD / exchange-rate conversion", "cmc_fiat_map + cmc_fiat_usd_snapshot", "DELIVERED (current snapshot)"),
        ("Global metrics (markets/exchanges)", "cmc_global_metrics + cmc_exchange_map", "DELIVERED"),
        ("CMC index - 'the 20 and the 100'", "cmc20_index + cmc100_index", "DELIVERED"),
        ("Monthly maintenance going forward", "src/cmc/maintain.py (idempotent)", "BUILT"),
        ("Full multi-year history / daily OHLCV", "requires plan upgrade", "BLOCKED by plan"),
    ]
    t = doc.add_table(rows=1, cols=3); t.style = "Light Grid Accent 1"
    hdr = t.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "Advisor asked for", "Where it is / how", "Status"
    for a, b, c in reqs:
        r = t.add_row().cells; r[0].text, r[1].text, r[2].text = a, b, c

    H(doc, "4. Dataset catalog (summary)", 1)
    t = doc.add_table(rows=1, cols=4); t.style = "Light Grid Accent 1"
    for i, h in enumerate(["Dataset", "Endpoint", "Rows", "Coverage"]):
        t.rows[0].cells[i].text = h
    for d in DATASETS:
        r = t.add_row().cells
        r[0].text = ("* " if d["star"] else "") + d["name"]
        r[1].text = d["endpoint"]; r[2].text = d["rows"]; r[3].text = d["coverage"]

    H(doc, "5. Dataset details (where stored + columns)", 1)
    doc.add_paragraph("All paths are relative to cmc_complete/data/ -- the single canonical "
                       "data folder (no other copy of this data exists in the project).")
    for d in DATASETS:
        H(doc, ("* " if d["star"] else "") + d["name"], 2)
        for label, val in [
            ("Stored at", "cmc_complete/data/" + d["file"]),
            ("CMC endpoint", d["endpoint"]),
            ("Rows", d["rows"]),
            ("Coverage", d["coverage"]),
            ("Entities", d["entities"]),
            ("Columns", d["cols"]),
            ("Status", d["status"]),
        ]:
            p = doc.add_paragraph()
            p.add_run(label + ": ").bold = True
            p.add_run(val)
        doc.add_paragraph(d["note"])

    H(doc, "6. Not available on this plan (and why)", 1)
    t = doc.add_table(rows=1, cols=3); t.style = "Light Grid Accent 1"
    hdr = t.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "Data / endpoint", "Path", "Reason"
    for a, b, c in BLOCKED:
        r = t.add_row().cells; r[0].text, r[1].text, r[2].text = a, b, c
    doc.add_paragraph(
        "The single most valuable locked item is daily OHLCV and full multi-year history - both "
        "require a higher CMC plan (Professional / Enterprise). Confirm the target tier lists "
        "'OHLCV Historical' with the desired lookback before upgrading.")

    H(doc, "7. How the data is stored & opened", 1)
    for line in [
        "Canonical format is Parquet (compact). A derived CSV export lives in "
        "cmc_complete/data/csv_export/.",
        "To (re)generate CSVs: cd cmc_complete/code && python3 convert_to_csv.py "
        "../data/coinmarketcap_data ../data/csv_export",
        "Every dataset directory has the .parquet, a manifest.json (source, coverage, counts, run "
        "metadata) and a raw_api_samples/ copy of a raw API response for reproducibility.",
        "Non-technical guide: cmc_complete/docs/HOW_TO_OPEN_THE_DATA.txt (SAS/Excel friendly).",
    ]:
        doc.add_paragraph(line, style="List Bullet")

    H(doc, "8. Code (extraction pipeline)", 1)
    doc.add_paragraph(
        "Live/importable code stays at its original repo path (Python imports need a fixed "
        "module path); every file below is also mirrored, read-only, into cmc_complete/code/ "
        "each time this script runs, except the keyless extractor, which now lives only inside "
        "cmc_complete/code/ (its standalone copy was consolidated away).")
    t = doc.add_table(rows=1, cols=2); t.style = "Light Grid Accent 1"
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Path", "Purpose"
    for a, b in CODE_FILES:
        r = t.add_row().cells; r[0].text, r[1].text = a, b
    doc.add_paragraph(
        "Design: a shared HTTP client + manifest + atomic idempotent storage layer, one collector "
        "per endpoint, and a maintenance orchestrator. The API key is read from the CMC_API_KEY "
        "environment variable and is never committed.")

    H(doc, "9. Monthly maintenance (going forward)", 1)
    doc.add_paragraph(
        "python -m src.cmc.maintain appends the latest month across datasets idempotently, "
        "writing directly into cmc_complete/data/coinmarketcap_data/ - re-running never "
        "duplicates rows (verified: running twice leaves row counts unchanged). This realises "
        "the advisor's goal of building history forward one month at a time without "
        "re-downloading existing data. Schedule it monthly via cron or a scheduled agent.")

    H(doc, "10. Reproducibility & credits", 1)
    for line in [
        "Total rows across all datasets: ~6,413,031.",
        "Credits used this extraction: ~62,176 of 150,000 monthly.",
        "The full daily-quotes pull alone used 59,461 credits across 904 API calls (batched 9 coins/call).",
        "Every dataset is regenerable from its collector + retained raw samples.",
    ]:
        doc.add_paragraph(line, style="List Bullet")

    doc.save(OUT_DOCX)
    return OUT_DOCX


def copy_into(src: Path, dst: Path):
    if not src.exists():
        return
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def build_bundle(docx_path: Path):
    (BUNDLE / "code").mkdir(parents=True, exist_ok=True)
    (BUNDLE / "data").mkdir(parents=True, exist_ok=True)
    (BUNDLE / "docs").mkdir(parents=True, exist_ok=True)

    # Sync code copies from their LIVE, importable locations. These stay in the main repo
    # (Python imports need a fixed module path) -- cmc_complete/code/ holds read-only
    # mirrors for a self-contained deliverable. Never wiped, only overwritten in place.
    copy_into(ROOT / "src" / "cmc", BUNDLE / "code" / "src_cmc")
    copy_into(ROOT / "tests" / "cmc", BUNDLE / "code" / "tests_cmc")
    copy_into(ROOT / "providers" / "coinmarketcap.py", BUNDLE / "code" / "providers" / "coinmarketcap.py")
    copy_into(ROOT / "agents" / "universe_agent_cmc.py", BUNDLE / "code" / "agents" / "universe_agent_cmc.py")
    for script in ("build_cmc_docs.py", "build_cmc_history.py",
                   "build_cmc_quotes_history.py", "build_cmc_web_history.py"):
        copy_into(ROOT / "scripts" / script, BUNDLE / "code" / "scripts" / script)
    # keyless_extractor/ and convert_to_csv.py live permanently inside cmc_complete/code/
    # now -- their old standalone source folders (coinmarketcap_extract/, cmc_all_data/)
    # were consolidated away, so there is nothing external left to sync them from.

    # Sync docs from their live (git-tracked) source locations.
    copy_into(ROOT / "docs" / "COINMARKETCAP.md", BUNDLE / "docs" / "COINMARKETCAP.md")
    copy_into(ROOT / "specs" / "001-cmc-data-pipeline", BUNDLE / "docs" / "spec_001-cmc-data-pipeline")
    # HOW_TO_OPEN_THE_DATA.txt lives permanently in cmc_complete/docs/ (no external source).

    # data/ is intentionally NOT touched here: cmc_complete/data/ is the single, permanent
    # home for every CMC dataset. Collectors (src/cmc/maintain.py) write there directly.
    # docx_path already IS BUNDLE/cmc_docs.docx (build_docx() saves there directly), so
    # there is nothing to copy in for it.

    (BUNDLE / "README.txt").write_text(
        "CMC COMPLETE - single canonical folder for everything CoinMarketCap\n"
        "====================================================================\n"
        "cmc_docs.docx              - full documentation of every dataset (start here)\n"
        "data/coinmarketcap_data/   - all 17 datasets: Parquet + manifest + raw API sample each\n"
        "data/csv_export/           - CSV versions of every dataset (Excel/SAS friendly)\n"
        "data/keyless_daily_extraction/ - the separate DAILY (not monthly) keyless extraction\n"
        "code/                      - read-only mirrors of every CMC-related script (see docs\n"
        "                              section 8 for which ones also live at their live repo\n"
        "                              path, since Python imports need a fixed module path)\n"
        "docs/                      - COINMARKETCAP.md, HOW_TO_OPEN_THE_DATA.txt, the spec\n"
        "\nNote: the API key is NOT included (kept in a git-ignored .env). Set CMC_API_KEY to "
        "run collectors.\n"
        "\nThis is the ONLY copy of CMC data in the project -- there is no cmc_all_data/, "
        "coinmarketcap_data/, or coinmarketcap_extract/ elsewhere; those were consolidated "
        "here.\n")
    return BUNDLE


if __name__ == "__main__":
    dx = build_docx()
    print("wrote", dx, f"({dx.stat().st_size/1024:.0f} KB)")
    b = build_bundle(dx)
    total = sum(f.stat().st_size for f in b.rglob("*") if f.is_file())
    nfiles = sum(1 for f in b.rglob("*") if f.is_file())
    print(f"bundle {b}: {nfiles} files, {total/1e6:.0f} MB")
