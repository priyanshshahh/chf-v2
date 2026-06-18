# CoinMarketCap (CMC) — Complete Reference

Everything CHF uses CoinMarketCap for: which APIs and endpoints, what data we actually
pulled (with real row counts and date ranges), the limitations we hit (tested live, not
from marketing copy), credits/rate limits, how each dataset feeds the pipeline, the code
that talks to CMC, and the integrity rules. CMC is the **primary universe-membership
source** for CHF and the reason the production universe is survivorship-bias-free.

Companion data folder: [`../coinmarketcap_data/`](../coinmarketcap_data/) holds a
committed copy of every CMC dataset described here. Related: `docs/UNIVERSE_AGENT.md`
(how the data becomes the universe) and `docs/CMC_HISTORICAL_ACCESS_LIMITATION.md` (the
original probe transcript).

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

### 2.2 Pro `/v2/cryptocurrency/quotes/historical` ✅ (36 months on Hobbyist)
Daily `price` / `market_cap` / `volume_24h` per `cmc_id`, works for many delisted coins
inside the window. Used by `build_cmc_quotes_history.py`. **Credits**: 1 per 100 data points.

### 2.3 Pro `/v1/cryptocurrency/listings/historical` ⚠️ (Hobbyist = 1 month)
The "proper" Pro PIT-membership endpoint. Params CHF sends (`providers/coinmarketcap.py
::fetch_historical_listings`): `date, start=1, limit, convert=USD, sort=cmc_rank,
sort_dir=asc, cryptocurrency_type=all, aux=platform,tags,date_added,circulating_supply,
total_supply,max_supply,cmc_rank,num_market_pairs`. **Credit**: 1 per 100 coins.
On Hobbyist it returns **HTTP 400** beyond ~1 month ("plan allows 1 months of historical
access"). Used only for a 1-month proof sample (`build_cmc_history.py`).

### 2.4 Pro `/v2/cryptocurrency/ohlcv/historical` ❌ not in plan
True daily OHLCV per `cmc_id`. On Hobbyist: **HTTP 403, error code 1006** ("plan does not
support this endpoint"). `fetch_ohlcv_historical` exists in the provider but yields nothing
on the current key.

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
| `v2 quotes/historical` | ✅ **36 months** | daily price/mcap/vol; 1 credit / 100 points |
| `v1 listings/historical` | ❌ **1 month** | HTTP 400 "plan allows 1 months"; 1 credit / 100 coins |
| `v2 ohlcv/historical` | ❌ **not in plan** | HTTP 403 code 1006 |
| `map` / `map?listing_status=inactive` | ✅ | ≈1,756 inactive coins enumerable |

- **Rate limit (Hobbyist)**: ~30 req/min → build scripts pace at `--min-seconds 2.1–2.5`.
- **Credits**: ~150,000/month (soft). The 36-month quotes pull cost ≈3,300 credits.
- **Deeper Pro `listings/historical` tiers**: Standard = 3 mo · Professional = 12 mo ·
  **Enterprise = up to 6 yr** (the only Pro path to multi-year survivorship-free membership).

Probe transcript: `docs/CMC_HISTORICAL_ACCESS_LIMITATION.md`. Diagnostics:
`python scripts/probe_api_readiness.py --config configs/run_config.yaml`.

---

## 4. The data we actually got (real, on disk)

Committed copies live in [`../coinmarketcap_data/`](../coinmarketcap_data/); working copies
under `data/external/` (gitignored). Numbers are from the live files.

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
`coinmarketcap_data/*/raw_api_samples/*.json` — untouched response bodies (keyless
`listings_historical_2021-01-01_top300.json` & `2024-02-01`, and Pro
`listings_2026-06-01.json`) so the exact API shape is inspectable. No keys in them.

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
`coinmarketcap_data/`.

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
</content>
