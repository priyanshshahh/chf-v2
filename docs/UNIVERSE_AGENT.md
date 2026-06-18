# Universe Agent — Complete Reference

The Universe Agent is the **first node** of the CHF pipeline. It defines *which assets
the model is allowed to evaluate and trade at each point in time*. Every downstream
stage — market data, on-chain, features, labels, model, portfolio, backtest — consumes
its output. If the universe is survivorship-biased or leaks look-ahead information, the
backtest is invalid and the headline negative result (`alpha_verified=false`) cannot be
trusted. **Correctness here is the foundation of the whole research program.**

This document is the single, exhaustive reference for the Universe Agent: the unified
architecture, every data-source mode, the shared processing core stage-by-stage, every
eligibility gate (with real exclusion counts from the live build), the full output
schema, the config surface, the build scripts, the verifier, the actual production
dataset, the bugs fixed during development, and — importantly — a candid critical
assessment of **what may still be wrong and what the real limitations are** (§14).

> Status note: this document supersedes earlier per-variant descriptions. The three
> historical agents are now **one unified agent**; the production universe is the
> survivorship-bias-free `cmc_web_pit` build (66 monthly snapshots, 2021-01 → 2026-06).

---

## 1. Current production status (authoritative)

Values below are read directly from the live run manifest
(`data/raw/universe/universe_manifest.json`) and QA tear-sheet
(`data/raw/universe/data_quality_universe.md`):

| Field | Value |
|---|---|
| `source` | `cmc_web_pit` |
| `universe_mode` | `historical_cmc_web_monthly` |
| `survivor_only_universe` | **`false`** (survivorship-bias-free) |
| `uses_cmc_id` | `true` (membership keyed on stable `cmc_id`) |
| Coverage | **2021-01-01 → 2026-06-01** |
| Monthly snapshots | **66 created / 66 requested** |
| Eligible rows | **6,600** (100 per month, every month) |
| Unique assets over the window | **324** distinct `cmc_id`s |
| Per-month eligible (avg/min/max) | 100.0 / 100 / 100 |
| Provider | `coinmarketcap_web_historical` |
| Validation (`verify_universe_run.py`) | **PASS** |

That **324 unique assets cycle through a 100-name monthly top-list** is the direct,
measurable signature of survivorship-free membership: ~224 names churned in and out over
5.5 years. Concrete since-delisted/collapsed names retained in their historical snapshots
include **FTT** (FTX Token), **LUNA** (Terra), **CEL** (Celsius), **HT** (Huobi), **EOS**,
**MIOTA**, **ABBC**, **GNT**, plus rebrands like **MATIC** (→POL).

---

## 2. What "point-in-time" and "survivorship bias" mean here

A survivorship-bias-correct universe obeys three principles:

1. **Membership is defined as it existed at time _t_** — the top-N by market cap on the
   first of each month, *not* today's top-N.
2. **Symbols that later went inactive / delisted are included** in the months they were
   actually ranked.
3. **Only information available up to _t_ is used** — no look-ahead.

Two strengths of compliance:

- **Survivorship-resistant** — captures coins that *fell out* of the top-N but still
  trade. Achievable from historical *prices/market-caps* on free tiers.
- **Survivorship-free** — additionally captures coins **fully delisted** from the data
  provider. This requires historical *listings/membership*, not just historical prices.

The production universe is **survivorship-free** because the source returns the true
top-N *as of each historical date including since-delisted coins* (§5.1).

---

## 3. Unified architecture

```
main.py universe ─▶ UniverseAgent (agents/universe_agent.py)
                        │  prepare() → run() → persist()   [AgentBase lifecycle]
                        │
                        ├─ _resolve_source()  ──▶ picks one of 6 source modes (§5)
                        │
                        ├─ source builders (agents/universe_sources.py)
                        │     build_cmc_web_pit / build_cmc_listings_download /
                        │     build_local_dataset → SourcePlan(per-snapshot candidate frames)
                        │
                        └─ shared processing core (§6): classify → gate → exclude →
                              top-N → hash → coverage → persist artifacts (§8)
```

- **`agents/universe_agent.py`** — the single orchestrator. Subclasses `AgentBase`
  (`agents/base.py`): the base `execute()` wraps `prepare()`/`run()`/`persist()` with
  retries (exponential backoff), status tracking, logging, and the SQLite run registry
  (`metadata/agent_registry.db`). The agent never reimplements that lifecycle.
- **`agents/universe_sources.py`** — pluggable *source builders*. Each turns a real
  dataset/API into a `SourcePlan`: a list of per-snapshot **candidate frames** in the
  canonical `CANDIDATE_COLUMNS` schema. CMC-specific ingestion lives here so the agent
  stays source-agnostic.
- **`agents/universe_agent_free.py`** and **`agents/universe_agent_cmc.py`** — thin
  **back-compat shims**. Each just sets `universe.source` (`local_dataset` and
  `cmc_listings_download` respectively) and delegates to `UniverseAgent`. The pipeline
  (`main.py`, `pipelines/pipeline_runner.py`) imports **only** `UniverseAgent`. The
  `Free` shim additionally force-disables the maturity/tradability/on-chain gates,
  because a bare rankings dataset cannot reconstruct them at past dates.

CLI: `python main.py universe` (optionally `--section <name>` to merge a config section
over `[universe]`). Programmatic: instantiate `UniverseAgent(cfg)` and call `.execute()`.

---

## 4. Research-integrity contracts (do not violate)

- **No synthetic data, ever.** Every candidate row traces to a real row in a real
  dataset or API response. Empty/missing inputs **raise** rather than fabricate
  (`UniverseValidationError` / `UniverseSourceError`). `prepare()` rejects any output
  path containing `demo` when `fail_on_demo_data=true`.
- **No look-ahead in gates.** Maturity, on-chain coverage, and the as-of selection all
  compare against `snapshot_date` with `<=` semantics (§6, §7).
- **Survivorship disclosed in the manifest.** `survivor_only_universe`,
  `universe_mode`, `historical_snapshot_limitation`, and `limitations[]` are always
  written so downstream consumers and reviewers can see exactly what was built.
- **Output schema is a contract.** `market_data_agent`, `onchain_agent`,
  `feature_agent`, the dashboard, the API, and `verify_universe_run.py` all read the
  files in §8. Preserve the columns and the `(snapshot_date, symbol)` /
  `(snapshot_date, cmc_id)` keys.

---

## 5. Source modes

`universe.source` selects the data path. `auto` (default) prefers the deep
survivorship-free dataset, then a local dataset, then live providers.

| `source` | Mode name | PIT? | Survivor-free? | Key? | Notes |
|---|---|---|---|---|---|
| `cmc_web_pit` | `historical_cmc_web_monthly` | ✅ | ✅ | none | **Production.** Keyless CMC data-API snapshots, strict PIT gates. |
| `cmc_listings_download` | `historical_cmc_monthly` | ✅ | ✅ | CMC Pro | Downloaded `listings/historical` parquet (Hobbyist ≈ 1 month deep). |
| `cmc_listings_live` | `historical_cmc_monthly` | ✅ | ✅ | CMC Pro | Live Pro `listings/historical` calls (plan-gated; 400-blocked on Hobbyist). |
| `local_dataset` | `historical_free_monthly` | ✅ (membership) | ✅ | none | Any free historical rankings CSV/Parquet/JSON; gates can't be reconstructed. |
| `live_market` | `monthly` / `latest_snapshot_only` | ❌ | ❌ | none | Free providers — **current rankings only**; cannot build true history. |
| `auto` | resolves to one of the above | — | — | — | `cmc_web_pit` → `local_dataset` → `live_market`. |

`_resolve_source()` precedence: explicit non-`auto` value wins; else the legacy
`use_cmc_historical_listings: true` flag → `cmc_listings_live`; else under `auto`, the
on-disk `cmc_web_dataset_path` → `cmc_web_pit`, then `historical_dataset_path` →
`local_dataset`; else `live_market`.

### 5.1 `cmc_web_pit` (production)

Source: CoinMarketCap's **public, keyless** data-API that powers
`coinmarketcap.com/historical`:

```
GET https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical?date=YYYY-MM-DD&start=1&limit=1000&convert=USD
```

It returns the true top-N **as of any date back to 2013-05-05, including
since-delisted/inactive coins** — exactly what removes survivorship bias, with **no API
key and no plan upgrade**. Verified point-in-time accurate (BTC market cap ≈ $546B on
2021-01-01; LTC ranked #2 in 2014). Each row carries `id` (stable `cmc_id`), `cmcRank`,
`dateAdded` (PIT maturity), category `tags`, `numMarketPairs` (PIT tradability proxy),
and a PIT `quote` (price/marketCap/volume).

It is ingested once by **`scripts/build_cmc_web_history.py`** into
`data/external/cmc_web/cmc_web_listings_historical.parquet`, then consumed by
`build_cmc_web_pit()` → `_process_pit_snapshot()`. For each requested month-start the
builder takes the exact month-start row if present, otherwise the most recent snapshot
within `asof_staleness_days` (default 40) **at or before** the target (never after — no
look-ahead).

### 5.2 `cmc_listings_download` / `cmc_listings_live`

PIT membership from CMC Pro `listings/historical`, either downloaded to parquet
(`scripts/build_cmc_history.py`) or fetched live. Processed by `_process_cmc_snapshot()`.
**Gated by the CMC plan's listings depth** — on the current Hobbyist key this endpoint is
HTTP-400 limited to ~1 month (§9), so these modes are shallow. Gates other than
classification + positive-market-cap are *not* re-verified historically in these modes.

### 5.3 `local_dataset`

Any free historical rankings file (CSV/Parquet/JSON). Required columns (case-insensitive,
override via `universe.column_map`): `date`, `symbol`, `market_cap` — optional `name`,
`rank`, `volume_24h`, `price`, `categories`. For each month-start it takes an **as-of
cross-section** (each symbol's most recent row at/before the target, within
`asof_staleness_days`, default 45), ranks by market cap, and applies classification only.
Maturity/tradability/on-chain gates are disabled (recorded as a manifest limitation).

### 5.4 `live_market` (fallback)

Free providers via a waterfall: **CoinGecko → CoinPaprika → CoinCap → CryptoCompare**
(`provider_priority`; CoinCap is removed from the default list — its free endpoint is
retired). Free providers expose only the *current* ranking, so this mode can build
**only the latest snapshot**. If history is requested it honestly degrades to
`universe_mode=latest_snapshot_only` (or raises if `require_true_historical_rankings=true`
or `allow_latest_snapshot_only=false`) and records the limitation. It **cannot** fabricate
historical monthly rankings. In this mode the gates *are* live-checked: maturity from the
CoinPaprika registry (`started_at`), tradability against Coinbase/Kraken market lists, and
on-chain coverage against the CoinMetrics catalog.

---

## 6. The shared processing core (stage by stage)

All sources funnel through `_assemble_plan()`, which dispatches each snapshot's candidate
frame to one of three processors — `_process_pit_snapshot` (cmc_web_pit),
`_process_cmc_snapshot` (cmc listings), or `_process_snapshot` (market: local_dataset /
live_market). The PIT processor is the production path; its stages:

1. **Normalize** — uppercase/trim symbols, drop blanks, coerce `cmc_id` to `Int64`,
   coerce `market_cap_usd` / `volume_24h_usd` / `price_usd` to numeric (NaN→0). Stamp
   `snapshot_date`, `snapshot_year`, `snapshot_month`, `created_at_utc`, provider/source.
2. **Sort** — by `market_cap_usd` desc, then `market_cap_rank` asc (deterministic).
3. **Classify** — `_classification_flags` sets `is_stablecoin` / `is_wrapped` /
   `is_bridged` / `is_lst` / `is_synthetic_pegged` (§7.1).
4. **Maturity gate** — `is_mature_365d` from `first_seen_utc` (`dateAdded`) vs
   `snapshot_date` (§7.2).
5. **Tradability gate** — `is_exchange_tradable` from `num_market_pairs` PIT proxy (§7.3).
6. **On-chain gate** — `has_onchain_coverage` from the CoinMetrics catalog `min_time`
   as of the snapshot (§7.4).
7. **Liquidity gate** — `passes_liquidity` = `volume_24h_usd >= min_daily_volume_usd`
   (§7.5).
8. **Exclusion reasoning** — `_exclusion_reason` assigns the **first** failing
   `(stage, rule, reason)` in a fixed priority order, producing `is_eligible` plus
   `exclusion_stage` / `exclusion_rule` / `exclusion_reason`.
9. **Top-N selection** — eligible rows sorted by market cap, de-duplicated on `symbol`
   (downstream joins key on symbol even though membership keys on `cmc_id`), truncated to
   `final_universe_n` (100). Rows beyond the cut are flipped to ineligible with reason
   `outside_final_top_n`.
10. **Snapshot hash** — SHA-256 (16 hex) of the final set's
    `(snapshot_date, cmc_id, symbol, market_cap_usd, market_cap_rank)`; stamped as
    `snapshot_id` on every row for reproducibility.
11. **Minimum check** — if `len(eligible) < minimum_eligible_n` and
    `fail_on_low_eligible_count`, raise.
12. **Coverage row** — per-snapshot QA counts (candidates/eligible/excluded, per-reason
    counts, market-cap coverage %, limitations).

`_assemble_plan` concatenates all snapshots, computes summary stats
(`unique_assets_total`, monthly eligible avg/min/max), and returns the universe,
exclusions, coverage, membership, and snapshot-hash map.

---

## 7. Eligibility gates in detail (with real exclusion counts)

Real per-reason exclusion totals from the production build (66 snapshots, 300 candidates
each → 100 eligible; ~200 excluded/month). These tell you which gates actually bind:

| Exclusion reason | Total rows | ≈ per month | Gate |
|---|---:|---:|---|
| `outside_final_top_n` | 5,708 | 86.5 | top-N cut (§6.9) |
| `maturity_unverified` | 2,753 | 41.7 | maturity (§7.2) |
| `no_onchain_coverage` | 1,413 | 21.4 | on-chain (§7.4) |
| `stablecoin` | 1,159 | 17.6 | classification (§7.1) |
| `synthetic_or_pegged_asset` | 750 | 11.4 | classification (§7.1) |
| `below_min_volume` | 542 | 8.2 | liquidity (§7.5) |
| `liquid_staking_token` | 474 | 7.2 | classification (§7.1) |
| `wrapped_asset` | 384 | 5.8 | classification (§7.1) |
| `bridged_asset` | 17 | 0.3 | classification (§7.1) |
| *(tradability)* | 0 | 0.0 | tradability (§7.3) — **never binds** |

### 7.1 Classification (`_classification_flags` + `configs/universe_exclusions.yaml`)

Removes non-alpha-bearing pegged/derivative assets. **Precedence** (no substring-on-blob
matching — that historically mis-flagged things):

1. **EXACT category-tag-slug membership** — the precise classifier. Slugs:
   `stablecoin_tags`, `wrapped_tags`, `bridged_tags`, `lst_tags`, `synthetic_tags` (plus
   a back-compat `category_denylist` honored as exact-tag membership).
2. **NAME whole-word match** — backstop for tagless rows (e.g. "Wrapped Bitcoin"):
   `stable_name_words`, `wrapped_name_words`, `bridged_name_words`, `lst_name_words`,
   `synthetic_name_words` (matched against tokenized name words, not substrings).
3. **Exact-symbol denylist** — `denylist_symbols` (USDT, USDC, DAI, WBTC, STETH, PAXG, …);
   a denylisted symbol with no positive classification is marked synthetic/pegged as a
   catch-all.

Why exact-slug not substring: substring-against-a-tag-blob mis-flagged PoS L1s tagged
`staking` (ADA, NEAR) as LST and DEX tokens tagged `derivatives` (HYPE) as synthetic;
generic `tokenized-stock`/`tokenized-assets` tags appear as noise even on mega-caps (a
stray `tokenized-stock` on Chainlink). Those are deliberately excluded from the slug
lists. (Note: `universe_sources.CATEGORY_FLAG_RULES` carries an older substring rule set
folded in from the former free agent; the production PIT path uses the exact-slug
classifier in `_classification_flags`.)

### 7.2 365-day maturity (`_check_maturity`) — biggest quality filter (~42/mo)

Skipped if `require_365d_maturity=false`. PIT-correct: uses `first_seen_utc` (CMC
`dateAdded`) and returns `(snapshot_date − dateAdded).days >= 365`. In `live_market` mode
it falls back to a CoinPaprika `started_at`/`first_data_at` registry. Coins with no
known inception are treated as **not** mature (conservative → excluded).

### 7.3 Exchange tradability (`_check_tradability_pit`) — never binds in PIT mode

Skipped if `require_exchange_tradability=false`. PIT proxy:
`num_market_pairs >= min_market_pairs_for_tradability` (default **1**). True historical
exchange-listing dates are not reconstructible from a rankings snapshot, so the count of
active market pairs at the snapshot is the available PIT signal. In `live_market` mode it
instead checks Coinbase/Kraken public market lists (`ExchangeTradabilityProvider`).
**With the threshold at 1, essentially every top-300 coin passes — this gate excluded 0
rows in the production build** (see §14.4).

### 7.4 On-chain coverage (`_check_onchain_coverage_pit`) — second-biggest filter (~21/mo)

Skipped if `require_onchain_coverage=false`. PIT-correct in time: the asset must have
CoinMetrics community data **on or before** the snapshot date. Implemented via
`load_coinmetrics_min_times()` — one call to the CoinMetrics community catalog
(`/v4/catalog/assets`), mapping `SYMBOL → earliest min_time` across all metrics/freqs;
eligible iff `snapshot_date >= earliest`. Keyed on **symbol**, against **today's** catalog
(see the bias caveat in §14.2). In `live_market` mode, `_check_onchain_coverage` just
checks presence in the catalog (any time).

### 7.5 Liquidity floor (`passes_liquidity`) — ~8/mo

Active only if `require_min_volume=true` (production: **true**). `volume_24h_usd >=
min_daily_volume_usd` (production: **$1,000,000**). The volume is the snapshot's 24h
figure (which, under as-of selection, can be up to `asof_staleness_days` old).

### 7.6 Positive market cap & top-N

`market_cap_usd > 0` is required (else `missing_market_cap`). Finally the eligible set is
ranked by market cap and cut to `final_universe_n` (100); the remainder become
`outside_final_top_n`.

---

## 8. Output schema & artifacts (`data/raw/universe/`)

Written by every mode; consumed by all downstream stages, the dashboard/API, and the
verifier.

| File | Contents |
|---|---|
| `universe_monthly.parquet` | Eligible universe rows — the tradable set per snapshot (production: 6,600 rows) |
| `universe_membership.parquet` | Membership incl. excluded coins + reasons (churn record; written in cmc_id modes) |
| `exclusions_monthly.parquet` | Every excluded coin with `exclusion_stage` / `exclusion_rule` / `exclusion_reason` (+ `raw_category_tags`) |
| `universe_coverage_report.parquet` | Per-snapshot QA: candidate/eligible/excluded counts, per-reason counts, market-cap coverage %, `passed_validation` |
| `universe_manifest.json` | Full provenance (see fields below) |
| `data_quality_universe.md` | Human-readable QA tear-sheet (gate coverage, exclusion breakdown, per-snapshot counts, provenance, limitations) |
| `partitioned/year=YYYY/month=MM/*.parquet` | Hive-partitioned copy of the eligible universe for filter-pushdown reads (DuckDB `COPY … PARTITION_BY`) |

**`CORE_COLUMNS`** (`universe_monthly.parquet`): `snapshot_date`, `snapshot_year`,
`snapshot_month`, `snapshot_id`, `provider`, `provider_asset_id`, `cmc_id`, `coin_id`,
`symbol`, `name`, `slug`, `market_cap_rank`, `market_cap_usd`, `volume_24h_usd`,
`price_usd`, `is_active_at_snapshot`, `is_stablecoin`, `is_wrapped`, `is_bridged`,
`is_lst`, `is_synthetic_pegged`, `is_mature_365d`, `is_exchange_tradable`, `exchange`,
`exchange_symbol`, `has_onchain_coverage`, `onchain_coverage_source`, `is_eligible`,
`exclusion_reason`, `source`, `created_at_utc`.

**`MEMBERSHIP_COLUMNS`**: `snapshot_date`, `snapshot_month`, `cmc_id`, `symbol`, `name`,
`slug`, `market_cap_rank`, `market_cap_usd`, `is_eligible`, `exclusion_reason`, `source`.

**Manifest fields** include: `run_id`, `created_at_utc`, `config_hash`, `start_date`,
`end_date`, `universe_mode`, `source`, `uses_cmc_id`, `survivor_only_universe`,
`provider`, `requested_start_date`/`requested_end_date`,
`actual_start_date`/`actual_end_date`, `historical_snapshots_requested`/`_created`,
`latest_snapshot_created`, `historical_snapshot_limitation`,
`survivorship_bias_disclosed`, `candidate_n`, `final_universe_n`, `minimum_eligible_n`,
`providers_used`, `cache_enabled`, `force_refresh`, `cache_hit_count`,
`api_call_count_by_provider`, `failed_api_call_count_by_provider`,
`monthly_snapshot_count`, `total_eligible_rows`, `total_excluded_rows`,
`unique_assets_total`, `average/min/max_monthly_eligible_count`, `output_files`,
`snapshot_hashes`, `warnings`, `limitations`.

Keys: `(snapshot_date, symbol)` always unique; additionally `(snapshot_date, cmc_id)`
in cmc_id modes. Timestamps are UTC-aware.

---

## 9. Verified API / plan limits (empirical, tested live)

### CoinMarketCap public data-API (keyless) — the production source
- `data-api/v3/cryptocurrency/listings/historical` — **true PIT top-N back to
  2013-05-05, incl. inactive/delisted**, no key. This is what makes the production
  universe survivorship-free without a plan upgrade.

### CoinMarketCap Pro API — current key is **Hobbyist** (`CMC_API_KEY`)
- Rate 300 req/min; ~150,000 credits/month.
- `v2/cryptocurrency/quotes/historical` — ✅ **36 months** of daily price/mcap/vol.
- `v1/cryptocurrency/listings/historical` — ❌ **1 month only** (HTTP 400, "plan allows
  1 months"). Binding limit for the *Pro* PIT-membership path → `cmc_listings_*` modes
  are shallow on Hobbyist.
- `v2/cryptocurrency/ohlcv/historical` — ❌ not in plan (HTTP 403, code 1006).
- `map?listing_status=inactive` — ✅ enumerates delisted coins.
- Deeper Pro `listings/historical`: Standard = 3 mo, Professional = 12 mo,
  Enterprise = up to 6 yr.

### CoinGecko (free + optional Demo key)
- `/coins/markets` (rankings) and `/coins/{id}/market_chart` (daily mcap/price/vol),
  **history capped at 365 days**, no PIT listings. No key → heavy 429 throttling; Demo
  key → ~30 req/min.

See `docs/COINMARKETCAP.md` for the full probe transcript.

---

## 10. Build scripts (`scripts/`)

| Script | Source | Produces |
|---|---|---|
| `build_cmc_web_history.py` | **keyless** CMC data-API historical listings | `data/external/cmc_web/cmc_web_listings_historical.parquet` (+ manifest) — **the production input** |
| `build_cmc_history.py` | CMC Pro `listings/historical` (+ `ohlcv`) | `cmc_listings_historical.parquet` (plan-gated; `--check-plan` prints limits/credits for free) |
| `build_cmc_quotes_history.py` | CMC Pro `quotes/historical` | `cmc_quotes_history.parquet` + `cmc_prices_history.parquet` (≤36 mo) |
| `build_coingecko_history.py` | CoinGecko free | `coingecko_history.parquet` + `coingecko_prices.parquet` + `coingecko_categories.json` (≤365 d) |

`build_cmc_web_history.py` columns: `snapshot_date, cmc_id, rank, symbol, name, slug,
market_cap_usd, price_usd, volume_24h_usd, circulating_supply, total_supply, max_supply,
num_market_pairs, date_added, raw_category_tags, source`. All builders cache raw JSON on
disk (resumable — re-runs skip cached dates), pace to the rate limit, never back-fill
missing dates, and write a manifest.

---

## 11. Verifier (`scripts/verify_universe_run.py`)

`python scripts/verify_universe_run.py [--section <name>]` → prints `Universe
validation: PASS|FAIL`. It re-reads the artifacts with DuckDB and asserts, among others:

- Required files present (membership required in cmc_id modes); no `demo` paths.
- `universe_monthly` non-empty; every snapshot `eligible_count >= minimum_eligible_n`.
- **No** `is_stablecoin/is_wrapped/is_bridged/is_lst/is_synthetic_pegged = true` rows
  leaked into the eligible set.
- Tradability/on-chain `false` rows absent when those gates are required (non-cmc modes).
- `market_cap_usd > 0`; `snapshot_id` non-null; unique `(snapshot_date, symbol)` and
  `(snapshot_date, cmc_id)`; non-null `cmc_id` in cmc modes.
- Coverage `passed_validation` all true; manifest has the required provenance fields and
  `actual_start/end_date` matching the data.
- If snapshots requested > created, `universe_mode` must be `latest_snapshot_only` and
  the limitation string must be present.
- **cmc_id modes**: `survivor_only_universe` must be `false`, mode must be a PIT mode,
  and `historical_snapshots_created >= min_snapshots_required` (default 24; production
  has 66).

The same checks run inside `UniverseAgent._validate_outputs()` at persist time, so a bad
build fails fast before any downstream stage runs.

---

## 12. Config reference (`configs/run_config.yaml` → `universe:`)

Current production values (abridged to the meaningful knobs):

```yaml
universe:
  research_mode: true            # requires cache_enabled=true
  live_api_enabled: true
  source: auto                   # → cmc_web_pit when the dataset is present
  cmc_web_dataset_path: "data/external/cmc_web/cmc_web_listings_historical.parquet"
  asof_staleness_days: 40        # as-of window for a missing month-start row
  candidate_n: 500               # NOTE: stored dataset has 300/snapshot → effective 300 (§14.7)
  final_universe_n: 100          # the tradable top-N
  minimum_eligible_n: 50         # fail a snapshot below this (fail_on_low_eligible_count)
  snapshot_frequency: monthly
  start_date: "2021-01-01"
  end_date: null                 # → through the dataset's latest snapshot
  # gates
  exclude_stablecoins/wrapped/bridged/lst/synthetic_pegged: true
  require_365d_maturity: true
  require_exchange_tradability: true
  min_market_pairs_for_tradability: 1
  require_onchain_coverage: true
  onchain_coverage_sources: [coinmetrics, defillama]
  require_min_volume: true
  min_daily_volume_usd: 1_000_000
  # provider waterfall (live_market only)
  provider_priority: [coingecko, coinpaprika, cryptocompare]
  exchange_tradability_sources: [coinbase, kraken]
  # fail-fast guards
  fail_on_empty_month / fail_on_low_eligible_count / fail_on_demo_data /
  fail_on_provider_exhaustion: true
  # history-honesty switches
  require_true_historical_rankings: false
  allow_latest_snapshot_only: true
  # caching / rate limiting
  cache_enabled: true; force_refresh: false; request_timeout_seconds: 30
  min_seconds_between_requests: 2.0; max_retries: 5; backoff_base_seconds: 3
  output_dir: "data/raw/universe"; cache_dir: "data/cache"
```

A second section `universe_dev` exists for fixture-based offline runs
(`--section universe_dev`). Config sections starting with `universe` are merged over the
base `universe` section by `main.py` / the verifier.

---

## 13. Bugs fixed during development

1. **CMC listings sort bug** (`providers/coinmarketcap.py`): `fetch_historical_listings`
   used `sort_dir="desc"`, returning the **bottom-ranked junk** (rank ~8260 "MAGA"…)
   instead of the top-N. Fixed to `asc`. Test fixtures were pre-sorted, so tests never
   caught it — a reminder that fixtures can hide ordering bugs.
2. **Parquet list-column** round-trips tag columns as numpy arrays, breaking the
   classifier's `tags or []`; normalized to Python lists (`_normalize_tags` /
   `_as_tag_list`) with no base-code change.
3. **Substring classification false positives** — ADA/NEAR (`staking`)→LST,
   HYPE (`derivatives`)→synthetic, LINK (`tokenized-stock`)→stablecoin. Fixed by moving
   to exact category-tag-slug matching + whole-word name backstop + symbol denylist.
4. **Missing Hive partitioning** in `persist()` — added `_write_partitioned`.

---

## 14. Critical assessment — what may be wrong / real limitations

This is an honest review of the *current* PIT path, ranked by how much it could affect
the research conclusion. Items 14.1–14.4 are genuine correctness/bias concerns; the rest
are known constraints.

### 14.1 The on-chain-coverage gate can re-introduce survivorship bias into the *eligible* set
**Most important.** Membership is survivorship-free, but `_check_onchain_coverage_pit`
checks each coin against **today's** CoinMetrics community catalog. A coin fully delisted
*before* CoinMetrics ever covered it will be absent from today's catalog and therefore
fail the gate — so it is correctly *included in membership* but *excluded from the eligible
top-100*. Because the gate removes ~21 coins/month and these are disproportionately the
smaller/older/since-dead names, the **eligible** universe is biased back toward survivors
even though the **membership** file is clean. This is the subtlest and most consequential
issue: the survivorship-free property is partly undone at the gate stage. Mitigations: use
a frozen historical CoinMetrics catalog snapshot, relax the gate to "coverage required only
for the on-chain *feature* stage, not for universe membership," or treat on-chain coverage
as a feature-availability mask rather than an eligibility filter.

### 14.2 On-chain coverage is keyed on **symbol**, not `cmc_id`
`load_coinmetrics_min_times()` maps `SYMBOL → min_time`, and the gate looks up by symbol.
Crypto tickers collide and get reused (multiple coins have shared symbols across delisted
and active assets). A delisted coin can be wrongly credited with a *different* live coin's
CoinMetrics coverage (false positive), or vice-versa. For a survivorship-free universe that
deliberately contains ticker-reused/delisted names, this is a real correctness risk. The
membership key is `cmc_id` precisely to avoid this — but the on-chain join drops back to
symbol. Fix: resolve CoinMetrics assets to `cmc_id` (via a slug/name map) before joining.

### 14.3 Selection ranks by `market_cap_usd`, and there is **no anomaly / self-reported-cap guard**
Both the candidate cut (`_cmc_web_candidates` → `head(candidate_n)` by `market_cap_usd`)
and the final top-N rank by raw `market_cap_usd`, not by the dataset's `cmcRank`. CMC market
caps include self-reported/unverified supplies that occasionally spike implausibly (the
known `JSM ≈ $24B in Jul-2023` quirk). A single anomalous cap can bump a junk coin into the
top-100 for a month and evict a legitimate one, distorting that snapshot. There is still no
max-plausibility / self-reported-supply sanity filter. Fix: rank by `cmcRank` (CMC's own
de-noised rank) and/or add a market-cap plausibility guard.

### 14.4 The exchange-tradability gate is effectively a no-op in PIT mode
`min_market_pairs_for_tradability = 1` means "had ≥1 market pair anywhere on CMC." In the
production build this gate excluded **0 rows**. So "exchange-tradable" in PIT mode does not
mean "tradable on a reputable venue" — only-DEX or only-obscure-exchange coins pass. Unlike
`live_market` mode (which checks Coinbase/Kraken), the PIT path has no venue-quality floor.
Either raise the threshold, or rename the column to reflect that it is a "has-market-pairs"
flag, not a tradability guarantee.

### 14.5 Category tags are not strictly point-in-time
The historical data-API returns category `tags` that are essentially **current** tags, so
classification (stablecoin/wrapped/LST/…) of a 2021 snapshot uses ~2026 categorizations.
This is a mild look-ahead — it only affects *classification*, never returns/labels, and is
generally harmless (a stablecoin is a stablecoin) — but a coin that changed category, or a
delisted coin with no current tags, can be mis-classified historically.

### 14.6 As-of staleness can misdate membership at the month boundary
When the exact month-start row is missing, the builder accepts a snapshot up to
`asof_staleness_days` (40) old (always `<= target`, so no look-ahead). Membership at the
boundary can therefore be up to ~40 days stale, and the liquidity check uses that snapshot's
(possibly stale) 24h volume.

### 14.7 Config/data drift: `candidate_n: 500` but the stored dataset holds 300/snapshot
The config requests 500 candidates, but `cmc_web_listings_historical.parquet` was built with
`--top 300`, so the effective candidate pool is **300**. A larger pool would let more
gate-failures be back-filled toward the 100 target and would slightly change membership.
The two should be reconciled (rebuild the dataset at 500, or set `candidate_n: 300`).

### 14.8 Brittleness around `minimum_eligible_n`
With `final_universe_n=100`, `minimum_eligible_n=50`, and `fail_on_low_eligible_count=true`,
the heavy maturity (~42/mo) + on-chain (~21/mo) gates could, on a sparse early month, drop
eligibility below the floor and **fail the whole run**. It holds today (100/100 every month),
but the margin over the 50-floor in early-2021 is not guaranteed across rebuilds.

### 14.9 Single active universe slot
`data/raw/universe/` holds whichever mode ran last. The CoinGecko-12mo, CMC-quotes, and
cmc_web universes cannot coexist without per-source output dirs or a `--universe-set`
selector.

### 14.10 Other constraints
- On-chain coverage depends on the **community** CoinMetrics tier (sparser than Pro).
- `cmc_listings_*` modes are shallow on Hobbyist (1-month `listings/historical`).
- No real-time execution; research/education only.
- Deeper-than-2013 or alternative-provider PIT membership would need another verified
  inactive-inclusive listings source.

**Bottom line.** The Universe Agent produces a verified, real, *survivorship-free*
point-in-time membership over 5.5 years — a genuine and well-disclosed strength. The most
important open risks are **14.1** (the on-chain gate quietly re-survivoring the eligible
set) and **14.2** (symbol-keyed on-chain join), followed by **14.3** (mcap-anomaly guard).
None of these *invalidate* the negative alpha result — if anything they make the eligible
set cleaner/more survivor-leaning, which would *help* find alpha, so a negative result is
conservative — but they should be fixed and disclosed before any positive claim.

---

## 15. How to run / reproduce

```bash
# Production (survivorship-free, keyless): build the dataset once, then the universe
python3 scripts/build_cmc_web_history.py --start 2021-01-01 --end 2026-06-01 --top 300 --freq monthly
python3 main.py universe                      # source: auto → cmc_web_pit
python3 scripts/verify_universe_run.py        # → PASS

# Free local-dataset path (no keys; gates disabled)
python3 scripts/build_coingecko_history.py --top 250
python3 agents/universe_agent_free.py --dataset data/external/coingecko_history.parquet

# CMC Pro listings path (needs multi-month listings access; Hobbyist = 1 month)
python3 scripts/build_cmc_history.py --check-plan
python3 scripts/build_cmc_history.py --start 2026-06-01 --end 2026-06-01 --top 100
python3 agents/universe_agent_cmc.py --listings data/external/cmc/cmc_listings_historical.parquet
```

`data/` is gitignored — regenerate with the commands above. Exclusions live in
`configs/universe_exclusions.yaml`; config sections in `configs/run_config.yaml`.

---

## 16. What is left (prioritized)

1. **Fix 14.1** — stop the on-chain gate from re-survivoring the eligible set (frozen
   catalog, or move coverage to a feature-availability mask).
2. **Fix 14.2** — key the on-chain coverage join on `cmc_id`, not symbol.
3. **Add a market-cap anomaly guard / rank by `cmcRank`** (14.3).
4. **Make tradability meaningful** in PIT mode or rename the flag (14.4).
5. **Reconcile `candidate_n` with the dataset** (14.7) and rebuild at 500 if desired.
6. **Per-source universe slots** (14.9) + `main.py` subcommands for the shim agents.
7. **Deeper / alternative PIT membership** sources; MLflow snapshot-hash logging
   (currently in the manifest + SQLite registry only).
</content>
</invoke>
