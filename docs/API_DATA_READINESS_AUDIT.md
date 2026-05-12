# API/Data Readiness Audit

<!-- API_READINESS:START -->
## API Readiness

- Created at UTC: `2026-05-07T00:03:42.207195+00:00`
- Config: `configs/run_config.yaml`
- Secret handling: API keys are reported only as present/missing/masked; full secrets are never printed.

### Key Status
- `CMC_API_KEY`: present ***7085
- `COINMARKETCAP_API_KEY`: present ***7085
- `ETHERSCAN_API_KEY`: present ***18GY
- `DUNE_API_KEY`: missing
- `THEGRAPH_API_KEY`: missing
- `GRAPH_API_KEY`: missing
- `COINGECKO_API_KEY`: present ***Gayo
- `COINMETRICS_API_KEY`: missing
- `DEFILLAMA_API_KEY`: missing

### CoinMarketCap
- CMC key visible: `true`
- Recent-window `/v1/cryptocurrency/listings/historical` works: `true`
- 3-year `/v1/cryptocurrency/listings/historical` works: `false`
- Observed listings/historical access window: `1 month`
- Observed quotes/historical access window: `12 months`
- CMC OHLCV historical supported: `false`
- Professor-grade 3-year point-in-time universe ready: `false`
- Recommended universe mode: `latest_survivor_baseline_until_cmc_upgrade`
- `accessible_date_range_observed`: `[]`
- Error/limitation: `Your plan allows 1 months of historical access. Please upgrade your plan or choose a startDate that is newer than 2026-04-06T00:03:42.746Z.; Your plan allows 1 months of historical access. Please upgrade your plan or choose a startDate that is newer than 2026-04-06T00:03:42.805Z.; Your plan allows 1 months of historical access. Please upgrade your plan or choose a startDate that is newer than 2026-04-06T00:03:42.867Z.`
- Decision: do not proceed to CMC 3-year historical universe construction; proceed next with the latest-survivor/free-provider baseline and explicit survivorship-bias disclosure.

### Provider Probe Summary
- `coinmarketcap`: historical listings not confirmed
- `coinmetrics`: ok=`True`, status=`200`, reason=``
- `defillama`: ok=`True`, status=`200`, reason=``
- `etherscan`: ok=`True`, status=`200`, reason=`OK`
- `thegraph`: ok=`False`, status=`None`, reason=`GRAPH/THEGRAPH key or configured subgraph missing`
- `dune`: ok=`False`, status=`None`, reason=`DUNE_API_KEY or configured query_ids missing`
<!-- API_READINESS:END -->
<!-- PIPELINE_INPUT_READINESS:START -->
## Pipeline Input Readiness

- Created at UTC: `2026-05-06T22:18:26.273623+00:00`
- Config: `configs/run_config.yaml`

### Artifact Summary
| Artifact | Present | Rows | Symbols | Date/Snapshot Range | Duplicate Keys | Notes |
|---|---:|---:|---:|---|---:|---|
| `data/raw/universe/universe_monthly.parquet` | `False` | `` | `` | `` | `` |  |
| `data/raw/universe/universe_membership.parquet` | `False` | `` | `` | `` | `` |  |
| `data/raw/universe/universe_manifest.json` | `False` | `` | `` | `` | `` |  |
| `data/raw/market/market_ohlcv.parquet` | `True` | `113959` | `97` | `2020-11-06 00:00:00+00:00 to 2026-04-28 00:00:00+00:00` | `0` |  |
| `data/raw/market/market_coverage_report.parquet` | `True` | `100` | `100` | `` | `` |  |
| `data/raw/market/market_manifest.json` | `True` | `` | `` | `` | `` |  |
| `data/raw/onchain/onchain_observations.parquet` | `True` | `317232` | `47` | `2020-11-06 00:00:00+00:00 to 2026-04-28 00:00:00+00:00` | `0` |  |
| `data/raw/onchain/onchain_wide.parquet` | `True` | `66256` | `47` | `2020-11-06 00:00:00+00:00 to 2026-04-28 00:00:00+00:00` | `0` |  |
| `data/raw/onchain/onchain_manifest.json` | `True` | `` | `` | `` | `` |  |
| `data/features/full_features.parquet` | `False` | `` | `` | `` | `` |  |
| `data/features/full_features_pruned.parquet` | `True` | `113959` | `97` | `2020-11-06 00:00:00+00:00 to 2026-04-28 00:00:00+00:00` | `0` |  |
| `data/features/feature_manifest.json` | `True` | `` | `` | `` | `` |  |
| `data/labels/label_matrix.parquet` | `True` | `111049` | `97` | `2020-11-06 00:00:00+00:00 to 2026-03-29 00:00:00+00:00` | `0` |  |
| `data/labels/modeling_dataset.parquet` | `False` | `` | `` | `` | `` |  |
| `data/labels/label_manifest.json` | `True` | `` | `` | `` | `` |  |
| `data/predictions/model_predictions.parquet` | `True` | `232125` | `97` | `2022-12-06 00:00:00+00:00 to 2026-03-19 00:00:00+00:00` | `150640` |  |
| `data/predictions/model_leaderboard.parquet` | `True` | `12` | `` | `` | `` |  |
| `data/allocations/allocations_from_predictions.parquet` | `True` | `28121` | `77` | `2022-04-25 00:00:00+00:00 to 2026-03-03 00:00:00+00:00` | `0` |  |
| `data/backtests/backtest_summary.parquet` | `True` | `9` | `` | `` | `` |  |
| `data/backtests/benchmark_summary.parquet` | `True` | `5` | `` | `` | `` |  |
| `data/backtests/alpha_report.json` | `True` | `` | `` | `` | `` |  |

### Warnings
- universe_membership.parquet missing; historical point-in-time membership cannot be applied downstream.
- data/raw/universe/universe_monthly.parquet missing
- data/raw/universe/universe_membership.parquet missing
- data/raw/universe/universe_manifest.json missing
- data/features/full_features.parquet missing
- data/labels/modeling_dataset.parquet missing
- data/predictions/model_predictions.parquet has duplicate key rows: 150640
<!-- PIPELINE_INPUT_READINESS:END -->
