# API Keys And Data Sources

CHF can run parts of the pipeline with free/community providers, but coverage improves when optional keys are available. Keep keys local. Never commit `.env`, paste keys into code, or print secrets in logs.

## Where Keys Go

CHF loads environment variables from the shell and from a project-root `.env` file.

Example local `.env` format:

```text
CMC_API_KEY=your_local_key
COINGECKO_API_KEY=your_local_key
ETHERSCAN_API_KEY=your_local_key
GRAPH_API_KEY=your_local_key
THEGRAPH_API_KEY=your_local_key
DUNE_API_KEY=your_local_key
COINMETRICS_API_KEY=your_local_key
DEFILLAMA_API_KEY=your_local_key
```

Only add keys you actually have. `.env` is local-only and should remain ignored by Git.

## Key Summary

| Key | Status | Used For | Notes |
|---|---|---|---|
| `CMC_API_KEY` | Provider-mode only | CoinMarketCap readiness probes, recent historical listings, CMC provider modes | Current plan access blocked 3-year historical listings and historical OHLCV in this study. |
| `COINGECKO_API_KEY` | Optional | CoinGecko provider calls | Free access may work with rate limits; a key can improve reliability depending on plan. |
| `ETHERSCAN_API_KEY` | Optional enrichment | Etherscan on-chain enrichment | Not a core dependency for the verified baseline pipeline. |
| `GRAPH_API_KEY` | Optional enrichment | The Graph provider access | Use if configured subgraphs require it. |
| `THEGRAPH_API_KEY` | Optional enrichment | Alternate The Graph key name | The readiness probe checks both `THEGRAPH_API_KEY` and `GRAPH_API_KEY`. |
| `DUNE_API_KEY` | Optional enrichment | Dune provider access | Probe avoids executing large or paid queries by default. |
| `COINMETRICS_API_KEY` | Optional / usually not required | CoinMetrics data | CoinMetrics Community API worked without a key during readiness checks. |
| `DEFILLAMA_API_KEY` | Optional / usually not required | DeFiLlama TVL and protocol metrics | DeFiLlama endpoints worked without a key during readiness checks. |

## Provider Notes

### CoinMarketCap

CoinMarketCap is important for professor-grade point-in-time historical universe research because `/v1/cryptocurrency/listings/historical` can provide historical listings snapshots.

Observed limitation in this study:

- recent listings worked inside the allowed access window,
- 3-year listings history was blocked by plan access,
- quotes history was limited,
- historical OHLCV was unsupported by the current plan.

Therefore CHF currently documents the latest-survivor universe limitation and does not claim a full point-in-time historical universe.

### CoinMetrics

CoinMetrics Community endpoints can work without a key and are used for market/on-chain style metrics where available. Coverage varies by asset and metric.

### DeFiLlama

DeFiLlama can work without a key and is used for TVL/protocol data where available. Coverage is sparse for some assets.

### Etherscan, The Graph, Dune

These are optional enrichment providers. Missing keys should not be treated as a core pipeline failure unless a config section explicitly requires them.

## Check Current Access

Run:

```bash
python3 scripts/probe_api_readiness.py --config configs/run_config.yaml
```

Outputs:

```text
docs/API_DATA_READINESS_AUDIT.md
data/readiness/api_probe_results.json
```

The probe reports whether keys are present or missing without printing secret values.
