# CoinMarketCap (CMC) — Complete Reference

Everything CHF uses CoinMarketCap for: which APIs and endpoints, what data we actually
pulled (with real row counts and date ranges), the limitations we hit (tested live, not
from marketing copy), credits/rate limits, how each dataset feeds the pipeline, the code
that talks to CMC, and the integrity rules. CMC is the **primary universe-membership
source** for CHF and the reason the production universe is survivorship-bias-free.

Companion data folder: [`../cmc_complete/data/coinmarketcap_data/`](../cmc_complete/data/coinmarketcap_data/)
holds every CMC dataset described here (parquet + manifest + raw API sample per dataset);
[`../cmc_complete/`](../cmc_complete/) is the single canonical folder for all CMC data,
code copies, and docs — see `cmc_complete/README.txt`. Related: `docs/UNIVERSE_AGENT.md`
(how the data becomes the universe). The original CMC access-limitation probe
transcript is consolidated into this document (see the sections below).

---

## 0. TL;DR

- CHF talks to CMC through **two distinct APIs**:
  1. The **public, keyless data-API** (`api.coinmarketcap.com/data-api/v3/...`) that powers
     `coinmarketcap.com/historical`. **This is the production source.** It returns the true
     top-N ranking *as of any date back to 2013*, including since-delisted coins → no
     survivorship bias, **no API key, no plan upgrade**.
  2. The **Pro API** (`pro-api.coinmarketcap.com/v1|v2/...`, header `X-CMC_PRO_API_KEY`)
     on a **Hobbyist** key. Used for `quotes/historical` (36 months daily) and a 1-month
     `listings/historical` proof sample.
- The binding limitation: on Hobbyist, the **Pro** `listings/historical` endpoint — the
  "proper" PIT-membership endpoint — is **HTTP-400 capped at 1 month**. The keyless
  data-API made this irrelevant by providing the same information for free and deeper.
- Net result: **survivorship-free PIT universe, 2021-01 → 2026-06, 66 monthly snapshots,
  top-300 candidates, no key required.**

---

## 1. The two CoinMarketCap APIs CHF uses

| | Public data-API (keyless) | Pro API (keyed) |
|---|---|---|
| Base URL | `https://api.coinmarketcap.com/data-api/v3` | `https://pro-api.coinmarketcap.com` |
| Auth | **none** | `X-CMC_PRO_API_KEY` header (`CMC_API_KEY` in `.env`) |
| Powers | `coinmarketcap.com/historical` | the official paid product |
| Used in CHF for | **production universe membership** | quotes history + a listings sample |
| Code | `scripts/build_cmc_web_history.py` (raw `urllib`) | `providers/coinmarketcap.py` (`CoinMarketCapProvider`) |
| Plan-gated? | no | yes (current key = Hobbyist) |

---

## 2. Endpoints used (every one)

### 2.1 Keyless data-API — `data-api/v3/cryptocurrency/listings/historical` ✅ PRODUCTION
```
GET https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical
      ?date=YYYY-MM-DD&start=1&limit=1000&convert=USD
```
Returns the full ranked top-N **as of `date`**, back to **2013-05-05**, **including coins
that were ranked then but have since collapsed/delisted** (`cryptocurrency_type=all`
implicit). Each row carries: `id` (stable `cmc_id`), `cmcRank`, `name`, `symbol`, `slug`,
`numMarketPairs`, `circulatingSupply`/`totalSupply`/`maxSupply`, `dateAdded` (true
first-listing date), category `tags`, and a PIT `quote` (price / marketCap / volume24h).
**Verified point-in-time accurate**: BTC market cap **$546,001,594,837 on 2021-01-01**
(matches the dataset's first row exactly); LTC ranked #2 in 2014.
Ingested by `scripts/build_cmc_web_history.py`.

### 2.2 Pro `/v3/cryptocurrency/quotes/historical` ✅ (36 months on Hobbyist)
Daily `price` / `market_cap` / `volume_24h` per `cmc_id`, works for many delisted coins
inside the window. Used by `build_cmc_quotes_history.py`. **Credits**: 1 per 100 data points.
Note: the original access-probe (§9/§10) tested the sibling **v2** endpoint (also live and
functional), but production collection uses **v3** because it supports batching multiple
`cmc_id`s per request — far more credit-efficient when pulling thousands of coins.
**[Q&A: daily-end quotes only (`interval=daily`); NOT minute-to-minute — no intraday/minute-level
CMC data exists anywhere in this project. v2 vs v3: both are live endpoints; v2 is used only by
the access-probe script for testing, v3 is what every production collector calls.]**

### 2.3 Pro `/v1/cryptocurrency/listings/historical` ⚠️ (Hobbyist = 1 month)
The "proper" Pro PIT-membership endpoint. Params CHF sends (`providers/coinmarketcap.py
::fetch_historical_listings`): `date, start=1, limit, convert=USD, sort=cmc_rank,
sort_dir=asc, cryptocurrency_type=all, aux=platform,tags,date_added,circulating_supply,
total_supply,max_supply,cmc_rank,num_market_pairs`. **Credit**: 1 per 100 coins.
On Hobbyist it returns **HTTP 400** beyond ~1 month ("plan allows 1 months of historical
access"). Used only for a 1-month proof sample (`build_cmc_history.py`).
**[Q&A: little that's unique here — a single-date (2026-06-01) proof sample capped at 1 month
by the Hobbyist plan; the keyless `data-api/v3` listings source (§2.1) supersedes it for real
historical coverage.]**

### 2.4 Pro `/v2/cryptocurrency/ohlcv/historical` ❌ not in plan
True daily OHLCV per `cmc_id`. On Hobbyist: **HTTP 403, error code 1006** ("plan does not
support this endpoint"). `fetch_ohlcv_historical` exists in the provider but yields nothing
on the current key.
**[Q&A: correct, this would contain true daily transaction OHLCV (open/high/low/close/volume),
not just quotes. 24-hour days are UTC-normalized (00:00–23:59 UTC) per
`providers/coinmarketcap.py`'s `_to_utc_timestamp(...).normalize()`. Once unblocked, both active
and inactive/delisted coins would be included — the sibling `quotes/historical` collector
(§2.2) already builds its universe as active ∪ delisted-in-window coins, and
`src/cmc/collectors/ohlcv.py` is scaffolded to reuse the same approach; only a 2-coin (BTC/ETH)
proof pull has run so far.]**

### 2.5 Pro `/v1/cryptocurrency/map` ✅
Symbol/`cmc_id`/slug/`is_active` directory; `map?listing_status=inactive` enumerates
delisted coins (≈1,756 returned). `fetch_map` in the provider. Useful for expanding the
candidate set to fully-delisted coins (not yet wired into the production build — see §8).

### 2.6 Pro `/v1/key/info` ✅
Plan name + credit usage. `build_cmc_history.py --check-plan` calls it first so you see
limits/credits **before** spending anything (free call).

---

## 3. Verified plan limits & costs (Hobbyist key, tested live)

| Endpoint | Result on Hobbyist | Detail |
|---|---|---|
| `data-api/v3 listings/historical` (keyless) | ✅ unlimited depth, no key | back to 2013-05-05, incl. delisted |
| `v3 quotes/historical` | ✅ **36 months** | daily price/mcap/vol; 1 credit / 100 points |
| `v1 listings/historical` | ❌ **1 month** | HTTP 400 "plan allows 1 months"; 1 credit / 100 coins |
| `v2 ohlcv/historical` | ❌ **not in plan** | HTTP 403 code 1006 |
| `map` / `map?listing_status=inactive` | ✅ | ≈1,756 inactive coins enumerable |

- **Rate limit (Hobbyist)**: ~30 req/min → build scripts pace at `--min-seconds 2.1–2.5`.
- **Credits**: ~150,000/month (soft). The 36-month quotes pull cost ≈3,300 credits.
- **Deeper Pro `listings/historical` tiers**: Standard = 3 mo · Professional = 12 mo ·
  **Enterprise = up to 6 yr** (the only Pro path to multi-year survivorship-free membership).

Probe transcript: see the original-probe section below. Diagnostics:
`python scripts/probe_api_readiness.py --config configs/run_config.yaml`.

---

## 4. The data we actually got (real, on disk)

Committed copies live in [`../cmc_complete/data/coinmarketcap_data/`](../cmc_complete/data/coinmarketcap_data/);
working copies under `data/external/` (gitignored). Numbers are from the live files.

### 4.1 `cmc_web_listings_historical.parquet` — keyless data-API (PRODUCTION)
- **19,800 rows · 66 monthly snapshots · 2021-01-01 → 2026-06-01 · top-300/snapshot.**
- **1,195 unique `cmc_id`s · 1,200 unique symbols** (the churn that proves survivorship-free).
- `failure_count: 0`, `live_pages_fetched: 60`, `survivorship_bias_free: true`,
  `includes_inactive_delisted: true`, `synthetic_data: false`.
- Columns: `snapshot_date, cmc_id, rank, symbol, name, slug, market_cap_usd, price_usd,
  volume_24h_usd, circulating_supply, total_supply, max_supply, num_market_pairs,
  date_added, raw_category_tags, source`.
- Delisted/collapsed names retained in their historical months: **FTT** (2021-01…2022),
  **LUNA** (last seen 2025-03), **CEL, HT, EOS, MIOTA, ABBC, GNT**, etc.
- **[Q&A: confirmed MONTHLY, not daily — 66 snapshots; a separate DAILY extraction of the same
  keyless source also exists (§4.5), covering 2023-06-19 to 2026-06-17. No # of wallets/addresses
  is included — CMC doesn't provide this; the project's only address-count data (CoinMetrics
  `AdrActCnt`) is unrelated to CMC.]**

### 4.2 `cmc_quotes_history.parquet` + `cmc_prices_history.parquet` — Pro quotes/historical
- **225,854 rows each · 299 symbols · ~36 months daily.**
- `quotes`: `date, symbol, name, market_cap, price, volume_24h, categories`.
- `prices` (backtest-shaped): `date, symbol, close, volume, market_cap`.
- Earlier universe build input (the 36-snapshot `historical_free_monthly` universe,
  now superseded by the keyless 66-snapshot build).

### 4.3 `cmc_listings_historical.parquet` — Pro listings/historical (1-month sample)
- **100 rows · single snapshot 2026-06-01** (the Hobbyist 1-month ceiling).
- Proof that the Pro PIT path works; too shallow for the multi-year universe.
- Manifest `cmc_history_manifest.json`: `plan_history_window_hit: false`, `top_n: 100`.

### 4.4 Raw API-response samples
`cmc_complete/data/coinmarketcap_data/*/raw_api_samples/*.json` — untouched response bodies (keyless
`listings_historical_2021-01-01_top300.json` & `2024-02-01`, and Pro
`listings_2026-06-01.json`) so the exact API shape is inspectable. No keys in them.

### 4.5 `cmc_daily_listings_historical.parquet` — keyless daily 3-year extraction
A separate, **daily**-granularity extraction from the keyless data-API, produced by the
live subscription test in §10 and stored under
[`../cmc_complete/data/keyless_daily_extraction/`](../cmc_complete/data/keyless_daily_extraction/) (`processed/`):
- **218,643 rows · 1,095 daily snapshots · 2023-06-19 → 2026-06-17 · top-200/day.**
- **507 unique `cmc_id`s · 516 unique symbols** — ~300 names churned in/out over 3 years,
  the measurable signature of survivorship-free membership.
- Same column schema as §4.1, plus an `is_active` column (see caveat in §10.3).
- Coins present at the start but gone from the latest snapshot include **BUSD, AGIX, BLUR,
  ABBC, ANT, BAND, BTG, CELO**, etc.
- Companion `processed/extraction_manifest.json` (provenance: endpoint, span, snapshot
  counts, unique coins, failures) and `processed/cmc_daily_listings_historical.csv`.

---

## 5. How CMC data flows into the pipeline

```
build_cmc_web_history.py ──▶ cmc_web_listings_historical.parquet
        (keyless data-API)            │
                                      ▼
UniverseAgent  source: cmc_web_pit ──▶ universe_sources.build_cmc_web_pit()
                                      ──▶ _process_pit_snapshot()  (PIT gates)
                                      ──▶ data/raw/universe/*  (the tradable universe)
                                      ──▶ market/onchain/feature/label/model/portfolio/backtest
```

The keyless dataset's extra columns map straight onto the universe's PIT gates:
- `date_added` → 365-day **maturity** gate (point-in-time correct).
- `raw_category_tags` → stablecoin/wrapped/LST/RWA **classification**.
- `num_market_pairs` → **tradability** proxy.
- `cmc_id` → the **stable membership key** (survives ticker reuse/rebrands).

See `docs/UNIVERSE_AGENT.md` §6–§7 for the gate mechanics and §14 for caveats (e.g. the
on-chain gate keying on symbol, and selection ranking by `market_cap_usd` rather than
`cmcRank`).

---

## 6. The code that talks to CMC

| File | Role |
|---|---|
| `providers/coinmarketcap.py` (`CoinMarketCapProvider`) | Pro API client: `fetch_historical_listings`, `fetch_ohlcv_historical`, `fetch_map`. Caches via `CachedHttpClient`; reads `CMC_API_KEY`; raises `CoinMarketCapProviderError` if a live call lacks a key. |
| `scripts/build_cmc_web_history.py` | **Keyless** data-API ingester (raw `urllib`). Args: `--start (req) --end --top 300 --freq weekly|monthly --min-seconds 2.5 --min-rows 50 --force-refresh --fail-on-missing-snapshot`. Resumable (per-date JSON cache). |
| `scripts/build_cmc_quotes_history.py` | Pro `quotes/historical`. Args: `--top 300 --months 36 --convert USD --out … --categories-from … --force-refresh`. |
| `scripts/build_cmc_history.py` | Pro `listings/historical` (+ optional `ohlcv`). Args: `--start --end --months --top 100 --no-ohlcv --max-ohlcv-coins --check-plan --force-refresh`. `--check-plan` prints plan/credits via `/v1/key/info`. |
| `agents/universe_sources.py` | `build_cmc_web_pit` / `build_cmc_listings_download` turn the parquet into per-snapshot candidate frames. |
| `agents/universe_agent_cmc.py` | Back-compat shim → `source: cmc_listings_download`. |

Caching: each CMC response is cached on disk so re-runs are free and reproducible
(`data/cache/cmc_web/` ≈21 MB / 129 files; `data/cache/cmc_quotes/` ≈137 MB / 302 files).
Caches are gitignored; the normalized parquet outputs are what travel in
`cmc_complete/data/coinmarketcap_data/`.

---

## 7. Integrity rules (do not violate)

- **No synthetic data.** Every CMC row parses from a real response; dates that fail to
  return a credible list are recorded as `failures` and **skipped**, never back-filled.
- **No look-ahead.** The keyless endpoint is queried *at* each historical date; the
  universe's as-of selection only accepts snapshots `<=` the target month-start.
- **Survivorship disclosed.** Manifests carry `survivorship_bias_free`,
  `includes_inactive_delisted`, and `synthetic_data:false`.
- **Keys never committed.** Only response bodies are stored; `CMC_API_KEY` stays in `.env`.

---

## 8. Limitations & open items (CMC-specific)

1. **Hobbyist caps the Pro PIT endpoint to 1 month** — mitigated entirely by the keyless
   data-API, but the Pro `listings/historical` path stays shallow unless upgraded.
2. **Keyless dataset is top-300 candidates**, so coins that were *never* in the top-300 but
   later mattered are out of scope; and config `candidate_n: 500` exceeds the stored 300
   (reconcile — rebuild at 500 or set `candidate_n: 300`). See `UNIVERSE_AGENT.md` §14.7.
3. **Fully-delisted coins below the ranking** — `map?listing_status=inactive` could expand
   the candidate set to delisted names by `cmc_id`, removing residual bias, at extra credit
   cost. Not yet wired into the production build.
4. **`ohlcv/historical` unavailable** on Hobbyist (HTTP 403) — true OHLCV would need a plan
   upgrade; CHF uses `quotes/historical` daily close/mcap/vol instead.
5. **Category `tags` are ~current, not strictly PIT** — classification of old snapshots uses
   present-day tags (mild, classification-only; never affects returns/labels).
6. **No real-time/execution use** — CMC is research-history only here.

**Bottom line:** the keyless CMC data-API is the unlock — it gives CHF a real,
survivorship-free, point-in-time universe over 5.5 years for free, sidestepping every
Hobbyist Pro-plan limitation. The Pro key adds 36-month daily quotes; the rest of CMC's
paid depth (multi-year listings, OHLCV) is gated behind Standard/Professional/Enterprise
and is not required for the current research result.

---

## 9. The original probe & research decision

Before the keyless data-API was adopted, an initial probe established that the supplied
Pro key could *see* a CoinMarketCap plan but that the plan did not provide the historical
access needed for a three-year point-in-time universe. Non-secret errors observed at that
time:

- `/v1/cryptocurrency/listings/historical` → **HTTP 400** for `2023-05-01`, `2024-05-01`,
  and `2026-03-31`; CMC message: plan allows only **1 month** of historical access.
- `/v2/cryptocurrency/quotes/historical` → **HTTP 400** for `2023-05-01`–`2023-05-10`; CMC
  message at that time: plan allows only **12 months** of historical access. (A later test
  — §10 — found this window had been raised to 36 months / 3 years for `quotes/historical`.)
- `/v2/cryptocurrency/ohlcv/historical` → **HTTP 403**, CMC error code **1006** (plan does
  not support the historical OHLCV endpoint).

**Listings ≠ daily market history.** For three-year point-in-time universe construction
CHF needs `/v1/cryptocurrency/listings/historical`, because that endpoint returns the
assets *listed at a historical date*, including active and inactive tickers when available
— the required source for monthly Top-N historical membership snapshots. Historical quotes
can price assets *after* they are selected, but quotes alone cannot tell CHF which assets
belonged in the universe at each past month; using today's survivor list with old prices
would still be a latest-survivor universe and overstates historical tradability.

**Research decision recorded at the time of the probe:**
- Do not build/run the CMC three-year historical universe path under a plan that lacks the
  access — and do **not** fake point-in-time listings from current rankings, quote history,
  or free-provider latest snapshots.
- If no valid PIT source is available, the only acceptable fallback is the
  latest-survivor/free-provider baseline, with survivorship bias **explicitly disclosed in
  every report**, results treated as conditional on the latest eligible survivor universe,
  and no professor-grade point-in-time validity claimed.
- The requirement to unblock was one of: an upgraded CoinMarketCap plan with ≥3 years of
  `/v1/cryptocurrency/listings/historical` access, **or** another verified point-in-time
  listings source covering inactive + active assets with historical ranks/market caps.

**This requirement is now met** by the keyless data-API (§2.1), which satisfies the second
option. `professor_historical_universe_ready=true` for the `cmc_web_pit` source; the
`latest_survivor_baseline_until_cmc_upgrade` mode is no longer the recommended path. The
Pro `listings/historical` endpoint remains 1-month-capped on the Hobbyist plan, but it is
no longer the only path. Full probe transcript: see the original-probe section below.

---

## 10. Live subscription re-test & the 3-year daily extraction (2026-06-17)

The supplied subscription was re-tested live against `pro-api.coinmarketcap.com` on
**2026-06-17** to answer one question directly: *does this plan give the historical ticker
list?* **No — not via the Pro `listings/historical` endpoint.**

### 10.1 What was tested, and the result

| What was tested | Result |
|---|---|
| `/v1/key/info` | **HTTP 200** — rate limit **300/min** (the announced increase IS live), 150,000 credits/month, 2,363 used. |
| `/v1/cryptocurrency/listings/historical` for `2023-07-01`, `2024-06-01`, `2025-06-01`, `2026-05-01` | **HTTP 400** — *"Your plan allows 1 months of historical access… choose a startDate that is newer than 2026-05-17."* |
| `/v1/cryptocurrency/listings/historical` boundary test | **`2026-05-18` → HTTP 200**, **`2026-05-16` → HTTP 400** → window confirmed still **~1 month**, not 3 years. |
| `/v2/cryptocurrency/quotes/historical` (BTC) for `2023-07` (3 yr back) | **HTTP 200** — daily points returned. |
| **Keyless** `data-api/v3/.../listings/historical` for `2023-07-01` | **HTTP 200** — BTC, ETH, USDT, BNB, USDC … (works, free, no key). |

**Interpretation.** CMC's "Hobbyist now includes 3 years of *daily* historical data" claim
is **true for `/v2 quotes/historical`** (per-coin daily price/market-cap/volume, verified 3
years back) and the **300/min rate-limit raise is genuine**. But the historical *listings*
(ticker-list) endpoint — which returns *which* active **and inactive** coins were ranked at
a past date — was **NOT** upgraded; it stays hard-capped at **1 month**. This is the
critical distinction for look-ahead-free research: **listings (membership) ≠ quotes
(per-coin prices).** On the current subscription the Pro `listings/historical` path
therefore **cannot** build a 3-year historical ticker list.

### 10.2 The extractor we built

`cmc_complete/code/keyless_extractor/extract_cmc_daily_history.py` — a self-contained,
resumable extractor that pulls **daily** historical top-N snapshots from the keyless
data-API and writes a tidy combined table + raw JSON + a provenance manifest into
`cmc_complete/data/keyless_daily_extraction/`:

- **Granularity** `--freq daily` (also `weekly`/`monthly`); default span 3 years back.
- **Coverage** `--top 200` default (≥100 plus churn headroom so the historical top-100 is
  always recoverable, including coins that later left the top-100).
- **Integrity** no synthetic data; a date that fails returns to `failures` and is skipped,
  never back-filled. Each day's raw response is cached → runs are resumable, re-runs free.
  No API key is read or written (this endpoint is keyless).
- **Robustness** handles both observed response shapes (`data:[...]` and
  `data:{cryptoCurrencyList:[...]}`) and both quote shapes (`quotes[0].marketCap` and
  `quote.USD.market_cap`).

Run used to produce the data, and reproduction/extension commands:
```bash
# 3 years daily (what we ran)
python3 cmc_complete/code/keyless_extractor/extract_cmc_daily_history.py --years 3 --top 200 --min-seconds 1.2

# explicit window
python3 cmc_complete/code/keyless_extractor/extract_cmc_daily_history.py --start 2023-06-17 --end 2026-06-17 --top 200

# "entire history" later — monthly back to CMC's 2013 origin (daily would be ~4,700 calls)
python3 cmc_complete/code/keyless_extractor/extract_cmc_daily_history.py --freq monthly --start 2013-05-05 --top 300
```
The same keyless endpoint reaches back to **2013-05-05**. Start monthly (cheap, ~157
months) to map coverage, then densify to daily for the windows the model needs; everything
is cached, so densifying only fetches the missing dates.

### 10.3 What was extracted (real, from `extraction_manifest.json`)

| Metric | Value |
|---|---|
| Frequency / depth | **daily**, top-200/day |
| Coverage | **2023-06-19 → 2026-06-17** |
| Daily snapshots built | **1,095** (of 1,096 requested) |
| Total rows | **218,643** |
| Unique coins (`cmc_id`) | **507** |
| Unique symbols | **516** |
| Live API calls | 1,089 |
| Failures | **1** — `2026-06-18` (a future date with no data; correctly skipped, never fabricated) |

That 507 unique coins cycle through a 200-deep daily list is the measurable signature of
survivorship-free membership — ~300 names churned in and out over 3 years. A 7-day
verification slice ran first and confirmed real data (BTC #1, ETH #2, USDT #3 — 1,050 rows
/ 152 unique `cmc_id`s, 0 failures). Outputs land under
`cmc_complete/data/keyless_daily_extraction/processed/` (parquet + CSV + manifest) and
`cmc_complete/data/keyless_daily_extraction/raw_daily_json/YYYY-MM-DD.json` (per-day audit
trail). See §4.5 for the dataset summary.

> **Caveat on `is_active`:** the keyless endpoint returns `is_active=1` for every row,
> because each row reflects the snapshot date on which the coin *was* active/ranked. The
> delisted/inactive coverage is evidenced by **membership churn** (507 unique vs ~200/day),
> **not** by the `is_active` column — do not rely on `is_active` as a delisted flag.

Pattern provenance: this generalizes a working single-coin Bitcoin extractor (API loop →
save JSON → convert to CSV) to the full daily top-N ticker list and the keyless
historical-listings endpoint: **loop dates → cache raw JSON per date → parse → combined
CSV/Parquet.** Full test narrative: see the 3-year daily-extraction section below.

**Bottom line of the re-test:** the subscription does **not** give the 3-year historical
*ticker list* via the Pro endpoint (still 1 month); the 3-year-daily upgrade and 300/min
rate limit apply to `quotes/historical`. The full 3-year **daily** historical ticker list
— active + inactive coins with daily market data — was obtained via the free keyless
data-API, with resumable code to extract and extend it: **1,095 daily snapshots,
2023-06-19 → 2026-06-17, 218,643 rows, 507 unique coins.**

---

## 11. New Pro-API datasets (2026-07-01 build) — the extra data the professor asked for

Collected by the `src/cmc/` pipeline (`python -m src.cmc.maintain`), one directory per
domain under `coinmarketcap_data/`, each with `<name>.parquet` + `manifest.json` +
`raw_api_samples/`. All money is **USD**; all dates are **YYYY-MM-DD**; every dataset is
keyed on a stable id/date so the monthly maintenance job is idempotent (re-running never
duplicates rows). Row counts / coverage below are from the live build.

| # | Dataset (dir / file) | CMC endpoint | Key | Columns | Coverage | Rows | Status |
|---|---|---|---|---|---|---|---|
| 1 | `pro_api_map/cmc_map.parquet` | `/v1/cryptocurrency/map` (listing_status = active+inactive+untracked) | `cmc_id` | cmc_id, symbol, name, slug, is_active, first_historical_data, last_historical_data, source | 2010-07-13 → 2026-06-30 (first/last historical) | **37,127** (8,132 active / 28,995 inactive) | **Collected** |
| 2 | `pro_api_global_metrics_historical/cmc_global_metrics.parquet` | `/v1/global-metrics/quotes/historical` (interval=daily) | `date` | date, total_market_cap, total_volume_24h, altcoin_market_cap, btc_dominance, eth_dominance, active_cryptocurrencies | 2023-07-02 → 2026-07-01 | **1,096** | **Collected — plan-capped at 36 months** (degraded per FR-013; `achieved_full_history=false`) |
| 3 | `pro_api_index_historical/cmc_fear_greed.parquet` | `/v3/fear-and-greed/historical` | `date` | date, value, value_classification | 2023-06-29 → 2026-06-30 | **1,098** | **Collected** |
| 4 | `pro_api_index_historical/cmc_altcoin_season.parquet` | `/v1/altcoin-season-index/historical` (timeframe=90d) | `date` | date, altcoin_index, altcoin_marketcap | 2026-04-03 → 2026-07-01 | **90** | **Collected — endpoint only exposes 90d** (max available; noted in manifest) |
| 5 | `pro_api_exchange_map/cmc_exchange_map.parquet` | `/v1/exchange/map` (active+inactive) | `exchange_id` | exchange_id, name, slug, is_active, first_historical_data, last_historical_data, source | 2018-04-26 → 2026-07-01 (first/last historical) | **1,257** | **Collected** |
| 6 | `pro_api_fiat_map/cmc_fiat_map.parquet` | `/v1/fiat/map` (include_metals=true) | `fiat_id` | fiat_id, symbol, name, sign, source | reference table (no dates) | **97** | **Collected** — currency reference for USD-conversion work |
| 7 | `pro_api_ohlcv_historical/cmc_ohlcv_sample.parquet` | `/v2/cryptocurrency/ohlcv/historical` (daily, convert=USD) | `(cmc_id, date)` | cmc_id, date, symbol, open, high, low, close, volume, market_cap | — | **0** | **Sample path built — endpoint NOT in plan** (HTTP 403, code 1006). Errors recorded in manifest. |
| 8 | `pro_api_index_historical/cmc100_index.parquet` | `/v3/index/cmc100-historical` (daily; + `/v3/index/cmc100-latest`) | `date` | date, value, num_constituents, constituents_json | 2024-01-01 → 2026-07-01 | **913** | **Collected — "the 100"**. Earliest point this plan exposes is 2024-01-01. |
| 9 | `pro_api_index_historical/cmc20_index.parquet` | `/v3/index/cmc20-historical` (daily; + `/v3/index/cmc20-latest`) | `date` | date, value, num_constituents, constituents_json (id/symbol/weight + priceUsd/units) | 2024-01-01 → 2026-07-01 | **913** | **Collected — "the 20"**. Earliest point this plan exposes is 2024-01-01. |
| 10 | `pro_api_airdrops/cmc_airdrops.parquet` | `/v1/cryptocurrency/airdrops` (status ENDED/ONGOING/UPCOMING) | `airdrop_id` | airdrop_id, project_name, status, coin_id, coin_symbol, coin_name, start_date, end_date, total_prize, winner_count, link | 2021-05-01 → 2022-11-25 (by start_date) | **426** | **Collected**. `airdrop_id` = reassembled hex Mongo ObjectId. |
| 11 | `pro_api_fiat_fx/cmc_fiat_usd_snapshot.parquet` | `/v2/tools/price-conversion` (amount=1, id=2781 USD, convert_id per fiat) | `(fiat_id, snapshot_date)` | snapshot_date, fiat_id, fiat_symbol, usd_to_fiat_rate, source | 2026-07-01 (**current snapshot**) | **88** | **Collected — CURRENT snapshot only**. 88 of 97 fiats/metals return a rate; plan caps convert to 8 ids/call. No bulk historical FX on this plan (see LEFT). |
| 12 | `pro_api_dex_reference/cmc_dex_platforms.parquet` | `/v1/dex/platform/list` | `platform_id` | platform_id, name, short_name, platform_alias, chain_id, crypto_id, native_token_address, token/tx/address explorer URL formats, is_verified | reference table (no dates) | **116** | **Collected — reference only**. Token/pair/OHLCV DEX data is OUT OF SCOPE (see LEFT). |

### Mapping every professor ask → the endpoint that satisfies it

- **Historical listings incl. delisted coins (survivorship-free):** keyless
  `data-api/v3/.../listings/historical` (§2.1, production, 2013→now) **plus** the new
  `/v1/cryptocurrency/map` (#1) which enumerates all ~37k coins active+inactive with
  stable `cmc_id` and `is_active` — e.g. Terra Classic (LUNC, id 4172) is present with
  `is_active=false`.
- **Daily quotes (price / market cap / volume):** `/v3/cryptocurrency/quotes/historical`
  (§2.2, 36-month daily, already on disk: `cmc_quotes_history.parquet`).
- **id ↔ symbol ↔ name ↔ slug map:** `/v1/cryptocurrency/map` (#1).
- **USD / fiat exchange-rate reference:** `/v1/fiat/map` (#6) is the currency reference
  (id/symbol/sign incl. metals). CMC has no keyed historical fiat-FX time series on this
  plan; the fiat map is the reference table for USD-conversion/provenance work.
- **Global market metrics:** `/v1/global-metrics/quotes/historical` (#2).
- **CMC market index / sentiment:** `/v3/fear-and-greed/historical` (#3) and
  `/v1/altcoin-season-index/historical` (#4).
- **"The 100" (CMC100 index):** `/v3/index/cmc100-historical` (#8) — daily value +
  constituent basket (id/symbol/weight), 2024-01-01 → now.
- **"The 20" (CMC20 index):** `/v3/index/cmc20-historical` (#9) — daily value +
  constituent basket incl. per-constituent priceUsd/units, 2024-01-01 → now.
- **Exchange rate to USD:** `/v2/tools/price-conversion` (#11) gives the **current**
  USD→fiat rate for every fiat/metal in the fiat map (`usd_to_fiat_rate`); the fiat map
  (#6) is the id/symbol/sign reference.
- **Airdrops:** `/v1/cryptocurrency/airdrops` (#10) — all ENDED/ONGOING/UPCOMING events.
- **DEX network reference:** `/v1/dex/platform/list` (#12) — the on-chain platform table.

### What is LEFT / plan-limited

1. **Full multi-year daily OHLCV for all ~12k coins** — the `/v2/cryptocurrency/ohlcv/historical`
   endpoint returns **HTTP 403 (code 1006, "plan doesn't support this endpoint")** on the
   current key, so even the BTC/ETH/LUNC sample returns 0 rows (recorded in the manifest).
   Full OHLCV backfill requires a plan upgrade; it would be a separate long run of ~12,000
   coins × per-coin history, paced under the rate limit. Daily close/mcap/vol is already
   covered by `quotes/historical` in the interim.
2. **Global-metrics history before 2023-07-02** — capped at **36 months** on this plan
   (degraded gracefully; achieved coverage recorded in the manifest).
3. **Altcoin Season Index before 2026-04-03** — the endpoint only exposes **90 days**; no
   deeper history is available from CMC for this index.
4. **CMC100 / CMC20 index history before 2024-01-01** — regardless of `time_start`, the
   index endpoints return no point earlier than **2024-01-01** on this plan, and `count` is
   hard-capped at **10 points/call** (paged forward in 10-day windows to build the series).
5. **Historical fiat FX (USD→fiat time series)** — **NOT available in bulk** on this plan.
   `price-conversion` supports a `time` param but only one date per call and max **8 convert
   ids/call**, so only a **current snapshot** is collected (#11). A full historical FX series
   is a documented gap.
6. **On-chain DEX token / pair / OHLCV data** — **OUT OF SCOPE** for this research. Only the
   platform reference (#12) is collected; token/pair/liquidity/OHLCV DEX endpoints require
   key entitlements / return 5xx on this plan.
7. **Not available on this plan or out of scope (confirmed):** OHLCV,
   `price-performance-stats`, `exchange/*` `listings-latest`, `market-pairs`, `trending`,
   `listings/new`, `content`, `community`, `derivatives` (404), and on-chain DEX
   token/pair data. **All time-series history is capped at 36 months.**

### Reproduce / maintain

```bash
# one-off full (re)collection of a dataset
python -c "from pathlib import Path; from src.cmc.client import CMCClient; \
from src.cmc.collectors import map as m; m.run(CMCClient(), Path('cmc_complete/data/coinmarketcap_data'))"

# idempotent monthly maintenance across all datasets (append newest only, no dupes)
python -m src.cmc.maintain                       # all datasets, through today
python -m src.cmc.maintain --datasets global_metrics fear_greed --month 2026-07
```

The maintenance job reads each manifest's `coverage_end`, fetches only newer data, and
upserts by primary key (`keep="last"`), so a second run for the same month adds zero rows
and changes no existing rows (proven by `tests/cmc/test_storage_idempotent.py`).
