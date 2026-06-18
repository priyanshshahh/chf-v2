# On-Chain Agent — Complete Reference

The On-Chain Agent (`agents/onchain_agent.py`) is the **third node** of the CHF pipeline. It
ingests fundamental on-chain / DeFi metrics for the universe's assets (the intersection of the
universe with full-OHLCV, QA-passed market symbols), normalizes them to a daily UTC grid clipped
to each symbol's market calendar, QA-gates per asset, and writes long + wide + coverage +
manifest + quality outputs plus a partitioned copy.

This is the **single exhaustive reference**: output contract, research-integrity guards, the
provider stack, the fetch waterfall, the full lifecycle, the 6-phase overhaul, the limitation
register, the complete config surface, the verifier, and the full test inventory.

> **Status:** the 6-phase overhaul is complete. **40 on-chain tests pass** (30 research-mode +
> 10 `tests/test_onchain_pit.py`), the default `verify_onchain_run.py` contract holds, and every
> change is config-gated to legacy defaults and **reduces (never adds) fake/incorrect data**. The
> survivorship-free path is `--section onchain_pit`.

---

## 1. Output contract

Written to `data/raw/onchain/`:

| Artifact | Contents |
|---|---|
| `onchain_observations.parquet` | **Long** table — one row per `(symbol, date_ts, metric_name, source)` |
| `onchain_wide.parquet` | **Wide** pivot — one row per `(symbol, date_ts)`, one column per metric |
| `onchain_coverage_report.parquet` | Per-asset QA: provider availability flags, metrics fetched, row/day counts, date range, provider attempts + failure reasons, `passed_qa` |
| `onchain_manifest.json` | Provenance — `snapshot_id`, `data_content_hash`, `as_of_date`, per-provider asset counts, providers used/unavailable, API/cache counts, limitations |
| `data_quality_onchain.md` | Human-readable QA tear-sheet |
| `partitioned/year=…/month=…/*.parquet` | Hive-partitioned copy (DuckDB `COPY`, pandas fallback) |

### Schemas (exact, verified against code)

**`OBSERVATION_COLUMNS` (13):** `date_ts, symbol, metric_name, metric_value, source,
provider_asset_id, provider_metric_name, provider_entity_id, data_type, snapshot_id,
fetched_at_utc, is_forward_filled, is_incomplete_dropped`.

**`WIDE_COLUMNS` (26):** `date_ts, symbol, adr_active_count, tx_count, realized_cap_usd,
mvrv_current, nvt_adjusted, fee_total_usd, transfer_value_adjusted_usd, current_supply,
market_cap_usd, issuance_total_usd, chain_tvl_usd, protocol_tvl_usd, fees_usd, revenue_usd,
dex_volume_usd, stablecoin_mcap_usd, pool_tvl_usd, pool_apy, gas_used, transaction_count_proxy,
token_transfer_count_proxy, protocol_volume_usd, snapshot_id, fetched_at_utc`.

**`NON_NEGATIVE_METRICS` (19):** count/USD metrics that may never be negative (rejected in the
agent twice and in the verifier). Ratio metrics (`mvrv_current`, `nvt_adjusted`, `pool_apy`) are
deliberately excluded so they pass through unfiltered.

---

## 2. Research-integrity guards (do not violate)

- **No fabrication.** `is_forward_filled` is always `False`; nothing is forward-filled or
  synthesized — sparse metrics stay sparse (FeatureAgent lags them downstream). The verifier
  now *asserts* no forward-filled rows persist (Phase 6).
- **No-look-ahead calendar.** `_normalize_asset_observations` clips to `[requested_start,
  requested_end]`, drops `>= as_of_date`, and **restricts on-chain dates to the symbol's
  market-calendar days** so on-chain can't out-date market. The verifier independently enforces
  midnight-only timestamps + current-day drop.
- **No look-ahead vintage.** DeFiLlama pool metrics (a *current* snapshot stamped on a past date)
  are quarantined (Phase 3).
- **Negative-value rejection** for the 19 `NON_NEGATIVE_METRICS`.
- **No wrong-coin data.** Reused tickers (a symbol → >1 `cmc_id`) are refused rather than risk
  attaching the live coin's data to a delisted one (Phase 2).
- **Source allow-list.** The verifier asserts every `source ∈ {coinmetrics, defillama, etherscan,
  thegraph, blockchair, dune}`.

---

## 3. Provider stack

`PROVIDER_KEYS = [coinmetrics, defillama, etherscan, thegraph, blockchair, dune]`. Per the
production config, **enabled: coinmetrics, defillama, etherscan, thegraph**; **disabled:
blockchair, dune**. `provider_priority` orders the per-asset waterfall.

| Provider | Key? | Metrics | Resolution | Notes |
|---|---|---|---|---|
| **CoinMetrics** | keyless (community) | `AdrActCnt, TxCnt, CapRealUSD, CapMVRVCur, NVTAdj, FeeTotUSD, TxTfrValAdjUSD, SplyCur, CapMrktCurUSD, IssTotUSD` | symbol→coin_id→exact-name vs **today's** catalog (`coinmetrics.py:126`) | base-layer economics; 10 req / 6 s; **latest-revised series** (restatement vintage, #4) |
| **DeFiLlama** | keyless | `chain_tvl_usd, protocol_tvl_usd, fees_usd, revenue_usd, dex_volume_usd, stablecoin_mcap_usd, pool_tvl_usd, pool_apy` | hard-coded chain/protocol alias maps + live `token_symbol` match (`defillama.py`) | DeFi utility; pool metrics **quarantined** (#3) |
| **Etherscan** | `ETHERSCAN_API_KEY` | `gas_used, transaction_count_proxy, token_transfer_count_proxy` | config table `chains[SYMBOL]` (ETH/BNB/AVAX/MNT) — **safe** | chain-level proxies; honest `*_proxy` names |
| **The Graph** | `GRAPH_API_KEY` + subgraphs | `protocol_volume_usd, protocol_tvl_usd, protocol_fees_usd` | config table — safe | inert without `configured_subgraphs` |
| **Blockchair** | keyless | chain tx/volume/fee | config table — safe | **disabled; parser shape mismatches API** (#6) |
| **Dune** | `DUNE_API_KEY` + query_ids | one curated metric | config table — safe | disabled; inert |

Only **CoinMetrics + DeFiLlama** are the real keyless coverage sources by default.

---

## 4. Lifecycle (`prepare → run → persist`)

**`prepare()`** — make dirs; `_load_asset_requests()` (universe ∩ market) → assets (raise if
empty); compute the window (`backfill_days`/`as_of_date`); `_init_provider_run_status`
(per-provider `run_availability()`); set a snapshot_id keyed on `universe_snapshot:requested_end`.

**`run()`** — per asset: `_fetch_asset` (waterfall) → coverage row. Concatenate, finalize,
build wide, compute metrics, `_fatal_errors`.

**`persist()`** — re-serialize coverage JSON columns; if fatal errors, write QA md and raise;
else write observations/wide/coverage parquet + partitioned + manifest (with `data_content_hash`,
`as_of_date`) + quality md; log to MLflow (non-fatal).

### `_fetch_asset` waterfall
- **Ambiguity refusal (Phase 2):** if `is_ambiguous_ticker` and `refuse_ambiguous_ticker_resolution`,
  skip all providers, coverage reason `ambiguous_ticker_refused`.
- Else iterate `provider_priority` (per-asset timeout 180 s); skip permanently-unavailable
  providers and **cooldown-active** ones (Phase 5); `RateLimitError` → bounded cooldown,
  `ProviderUnavailableError` → permanent.

### `_normalize_asset_observations` (exact order)
1. UTC-normalize `date_ts`; numeric-coerce `metric_value`; `inf→NA`; drop NA.
2. Filter to this symbol; clip to `[start, end]`; drop `>= as_of_date` (Phase 5).
3. Drop negative `NON_NEGATIVE_METRICS`.
4. **Quarantine `lookahead_unsafe_metrics`** (pool_tvl_usd, pool_apy) (Phase 3).
5. Restrict to the symbol's market-calendar days.
6. Stamp `snapshot_id`, `fetched_at_utc`, `is_forward_filled=False`; dedup; reorder.

### QA gate & fatal errors
Per asset: `passed_qa = unique_days >= min_history` where `min_history` is 365 (absolute) or
`min_history_days_floor` (membership_aware, Phase 4); below → asset's data dropped. Run-level
`_fatal_errors`: empty coverage/observations, below `minimum_assets_with_any_onchain` (15),
below `minimum_total_metric_observations` (30000), DeFiLlama minimums, no provider attempts.

---

## 5. The 6-phase overhaul

1. **PIT collapse fix** — `universe_membership_mode: union_full_history` builds one request per
   stable `cmc_id` across all snapshots (dead coins included). Was a `snapshot_date.max()` collapse.
2. **Wrong-coin refusal (#2)** — reused tickers (symbol → >1 `cmc_id`) flagged `is_ambiguous_ticker`
   and refused (no provider call). Prevents attaching the live coin's data to a delisted coin.
3. **Pool look-ahead quarantine (#3)** — DeFiLlama `pool_tvl_usd`/`pool_apy` dropped (current
   snapshot stamped on a past date). `lookahead_unsafe_metrics`, opt-out.
4. **Membership-aware history (#5)** — `min_history_days_policy: membership_aware` →
   `min_history_days_floor` (90) keeps short-lived delisted coins.
5. **Determinism + cooldown (#7,#8)** — `as_of_date`, `data_content_hash`, bounded
   `provider_cooldown_seconds` (rate limits) vs permanent (geo/DNS).
6. **Cleanup + infra** — deleted the dead byte-identical `on_chain:` config dup; DuckDB
   `COPY PARTITION_BY (year,month)` (pandas fallback); MLflow `_log_to_mlflow`; verifier hardening
   (no forward-fill + source allow-list); this doc.

---

## 6. Limitation register

| # | Issue | Status |
|---|---|---|
| 1 | Survivorship collapse (latest-snapshot only) | **Fixed (P1)** |
| 2 | Wrong-coin via symbol + today's-catalog resolution | **Mitigated (P2)** — ambiguous tickers refused |
| 3 | DeFiLlama pool metrics look-ahead | **Fixed (P3)** |
| 4 | CoinMetrics restatement vintage | Open — inherent to community tier; documented |
| 5 | `min_history_days=365` drops whole asset | **Fixed (P4)** |
| 6 | Blockchair parser shape mismatch | Open — **disabled (inert)** |
| 7 | Run-global provider blacklist | **Fixed (P5)** |
| 8 | No determinism (`as_of_date`/content hash) | **Fixed (P5)** |
| 9 | Wide pivot `aggfunc="last"` mixes sources | Open — rare, low impact |
| 10 | Dead `on_chain:` config dup | **Fixed (P6)** |
| 11 | Verifier gaps (forward-fill/source) | **Partially fixed (P6)** |

Still open (honest): #4 (restatement), #6 (Blockchair, disabled), #9 (rare collision). None feed
incorrect data in the default or PIT config.

---

## 6A. Limitations & remaining work (the full honest picture)

The register above tracks the 11 audited issues. Beyond those, these are the **structural
limitations** that remain — none are fabrication/look-ahead bugs (those are fixed), but they
bound what the on-chain tier can claim.

### Structural limitations (inherent or by-design)

- **Coverage is sparse — on-chain is a secondary tier.** CoinMetrics community covers mainly
  major assets; DeFiLlama only curated chains/protocols. Most mid/long-tail and **most dead
  coins simply do not resolve** at either provider, so they get *no* on-chain data even in PIT
  mode. In the frozen baseline this was ~43/100 assets, and on-chain candidates never reached
  the backtest. Treat on-chain features as a secondary signal, not a primary one.
- **The wrong-coin fix (#2) is conservative, not a true resolution.** Reused-ticker assets are
  *refused* (zero data), not correctly mapped. A real fix needs a `cmc_id → provider-asset`
  bridge (CoinMetrics has no `cmc_id`; it would require a slug/name map). So reused-ticker dead
  coins are correctly-but-lossily dropped.
- **`cmc_id` is not persisted into observations; the downstream join is still symbol-keyed.**
  Phase 1–2 thread `cmc_id` through the *request* (and use it to refuse ambiguous fetches), but
  `onchain_observations.parquet` carries `symbol`, not `cmc_id`, and FeatureAgent joins on-chain
  to market/features by **symbol**. So the `cmc_id` correctness protects the *fetch*, not the
  *join* — if two coins share a symbol and one has on-chain data, the feature join could still
  mis-attribute. Persisting `cmc_id` and joining on it downstream is open work.
- **Chain-level metrics are attached to a token symbol.** Etherscan `gas_used`/`*_proxy` and
  DeFiLlama `chain_tvl_usd` describe the *chain*, not the token. Honest in Etherscan's `*_proxy`
  naming, less so for `chain_tvl_usd`. A token on a chain is not the chain.
- **CoinMetrics restatement vintage (#4).** The community endpoint always serves the
  *latest-revised* series, so a value at date *t* can embed later methodology — a mild
  restatement look-ahead, not flagged per-row. A frozen historical vintage would be needed to
  fully close it.
- **No cross-source reconciliation.** One source per metric; no corroboration. The wide pivot's
  `aggfunc="last"` (#9) silently picks one source on the rare same-metric collision.
- **Determinism is partial.** `as_of_date` + `data_content_hash` make the *data window* and
  *content* reproducible (Phase 5), but `fetched_at_utc` and MLflow run timestamps remain
  wall-clock metadata.
- **On-chain requires full-OHLCV market coverage.** Assets that fail the market full-OHLCV/QA
  gate are excluded from on-chain entirely, compounding sparsity for thin/dead coins.

### Unproven at runtime (the one real unknown)

- **The PIT path (`union_full_history`) has never run end-to-end live.** All fixes are
  unit/integration-tested and gated, but the dead-coin on-chain coverage is **unknown** — and
  CoinMetrics/DeFiLlama rarely cover dead alts, so it is likely very low. The run-level
  thresholds (`minimum_assets_with_any_onchain: 15`, `minimum_total_metric_observations: 30000`)
  could **fail the PIT run** if coverage is thin. Verify and, if needed, relax the thresholds in
  the `onchain_pit` section before committing to the long ingest.

### Remaining work (prioritized)

1. **Persist `cmc_id` into observations and join on it downstream** — closes the symbol-join
   mis-attribution gap and makes #2 a true fix, not just a fetch-time refusal.
2. **`cmc_id → provider-asset` bridge** — resolve ambiguous/dead coins instead of refusing them
   (turns the conservative refusal into real coverage).
3. **Flag/freeze CoinMetrics restated metrics** (#4) — per-row vintage flag or a frozen catalog.
4. **Fix or delete the Blockchair provider** (#6) — currently disabled with a broken parser.
5. **Wide pivot: dedup by source priority** instead of `aggfunc="last"` (#9).
6. **Verifier: validate wide-table values** (e.g. negative TVL in the wide table) and reconcile
   the Etherscan/TheGraph/Blockchair/Dune manifest counts (only CoinMetrics + DeFiLlama are today).
7. **Tag chain-level vs token-level metrics distinctly** (a `metric_scope` tag or rename).
8. **Run the live `onchain_pit` ingest**, confirm dead-coin coverage, and re-tune thresholds.

---

## 7. Complete config surface (`configs/run_config.yaml → onchain`)

**Production base (verified):** `provider_priority: [coinmetrics, defillama, etherscan, thegraph,
blockchair, dune]`; `backfill_days: 2000`; `min_history_days: 365`;
`minimum_assets_with_any_onchain: 15`; `minimum_total_metric_observations: 30000`;
`minimum_assets_with_defillama: 5`; `minimum_defillama_observations: 500`; `max_assets: null`;
enabled coinmetrics/defillama/etherscan/thegraph, disabled blockchair/dune.

**New gated keys (defaults = legacy):**
```yaml
onchain:
  universe_membership_mode: latest_snapshot       # | union_full_history   (P1)
  refuse_ambiguous_ticker_resolution: true        # (P2)
  lookahead_unsafe_metrics: [pool_tvl_usd, pool_apy]   # (P3)
  min_history_days_policy: absolute               # | membership_aware    (P4)
  min_history_days_floor: 90
  as_of_date: null                                # pin for reproducibility (P5)
  provider_cooldown_seconds: 60                   # (P5)
mlflow:
  log_onchain_run: true                           # (P6)
```
Ready-to-run **`onchain_pit`** section enables `union_full_history` + `membership_aware` +
`refuse_ambiguous_ticker_resolution`.

---

## 8. Verifier (`scripts/verify_onchain_run.py`)

Checks: all 5 files exist; no `demo` paths; the three exact column sets (13 observation, 6 required
wide, 27 coverage); non-empty observations/coverage; **midnight-only** `date_ts`; **current-day
drop**; numeric `metric_value`; **negative-value rejection**; dedup `(symbol, date_ts, metric_name,
source)`; coverage↔observation symbol-set consistency; coverage thresholds; manifest reconciliation
(CoinMetrics + DeFiLlama counts). **Phase 6 additions:** asserts **no forward-filled rows** persist
and every `source` is a known provider. `validate_onchain_outputs(cfg)` returns a failure list
(empty = PASS).

---

## 9. Run it (survivorship-free)
```bash
python main.py onchain --section onchain_pit          # after the market PIT ingest
python scripts/verify_onchain_run.py --section onchain_pit
```
On-chain intersects the full-OHLCV market symbols, so it needs the market PIT run first.

---

## 10. Test inventory

- `tests/test_onchain_pit.py` (**10**): union rescues dead coins / latest drops them /
  dedup-per-`cmc_id`; pool look-ahead quarantine + opt-out; reused-ticker flagged + refused
  (no provider call); membership-aware floor; `as_of_date` deterministic current-day drop;
  content-hash determinism + order-independence.
- `tests/test_onchain_agent_research_mode.py` (**30**): exact aliases, negative rejection,
  current-day drop, provider-unavailability non-fatal, no fabricated rows, min-history exclusion,
  no hardcoded symbols, verifier reconciliation, coverage thresholds. (Test `_cfg` disables MLflow
  for hygiene.)

**Total: 40 — all passing.**
