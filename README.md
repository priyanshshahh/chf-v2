# Project CHF — Crypto Alpha Research Pipeline

Project CHF is a reproducible quantitative crypto research pipeline that tests whether market and on-chain features contain tradable cross-sectional alpha after leakage-safe modeling, deterministic portfolio construction, transaction costs, benchmark sanity checks, and out-of-sample backtesting.

Start with the final research package in [docs/RESEARCH_RESULTS_SUMMARY.md](docs/RESEARCH_RESULTS_SUMMARY.md), then use [docs/REPRODUCIBILITY_COMMANDS.md](docs/REPRODUCIBILITY_COMMANDS.md) for reproduction notes.

## Research Question

Can market and on-chain features be used to construct crypto portfolios that outperform BTC, ETH, BTC/ETH 50-50, and an equal-weight crypto universe after costs and realistic validation?

## Current Research Result

- Signal search found statistically promising candidates.
- BacktestAgent tested candidate portfolios after transaction costs and benchmark sanity checks.
- `alpha_verified=false` for all tested candidates.
- No verified alpha found under tested configurations.
- Strongest candidate: `linear_ridge / market_only / raw_forward_return / 30d`.
- That candidate beat ETH and the equal-weight universe, but did not beat BTC or BTC/ETH 50-50.
- The latest-survivor universe limitation remains because CMC 3-year historical listings were blocked by current API plan access.

| Candidate | Best Strategy | Return | CAGR | Sharpe | Max DD | Alpha Verified |
|---|---|---:|---:|---:|---:|---:|
| lightgbm / market_only / raw_forward_return / 14d | top_20_vol_scaled | 45.39% | 12.10% | 0.5030 | -71.45% | false |
| linear_ridge / market_only / raw_forward_return / 30d | top_5_equal_weight | 147.36% | 31.84% | 0.7521 | -59.40% | false |
| random_forest / market_only / raw_forward_return / 14d | top_5_equal_weight | -30.40% | -10.47% | 0.2288 | -86.86% | false |

| Benchmark | Total Return |
|---|---:|
| BTC | 305.50% |
| ETH | 69.85% |
| BTC/ETH 50-50 | 178.04% |
| Equal-weight universe | 30.39% |

## Pipeline Architecture

```text
UniverseAgent
→ MarketDataAgent
→ OnChainAgent
→ FeatureAgent
→ LabelAgent
→ ModelAgent
→ AlphaResearchAgent
→ PortfolioAgent
→ BacktestAgent
```

- UniverseAgent: builds the eligible crypto universe.
- MarketDataAgent: ingests and validates OHLCV data.
- OnChainAgent: ingests CoinMetrics, DeFiLlama, and other provider metrics where available.
- FeatureAgent: builds leakage-safe market/on-chain features.
- LabelAgent: creates exact forward calendar labels.
- ModelAgent: performs purged walk-forward signal screening.
- AlphaResearchAgent: signal-only research expansion; cannot claim alpha.
- PortfolioAgent: creates deterministic allocations from prediction-only files.
- BacktestAgent: final alpha authority; verifies or rejects alpha after costs and benchmarks.

## Methodology Highlights

- Exact forward calendar labels.
- Leakage guards throughout feature, label, model, portfolio, and backtest stages.
- Purged and embargoed walk-forward validation.
- Cross-sectional rank metrics, including Rank IC and top/bottom spreads.
- Prediction-only portfolio inputs; realized returns and labels are rejected from allocation inputs.
- Transaction costs included in backtests.
- BTC, ETH, BTC/ETH 50-50, and equal-weight universe benchmarks.
- Benchmark sanity checks for date alignment and impossible benchmark behavior.
- No alpha claim unless BacktestAgent verifies it.

## Repository Layout

```text
agents/       deterministic pipeline agents
providers/    API/provider adapters
features/     feature engineering utilities
models/       walk-forward validation utilities
pipelines/    orchestration helpers
scripts/      readiness probes, verifiers, candidate export
configs/      run configuration and exclusions
tests/        unit/research-integrity tests and fixtures
docs/         final reports and reproducibility notes
schemas/      schema definitions
data/         generated locally; ignored by Git
metadata/     local runtime metadata; ignored by Git
```

## Quick Start

```bash
git clone https://github.com/priyanshshahh/chf.git
cd chf

python3 -m venv .venv
source .venv/bin/activate

python3 -m pip install -r requirements.txt

cp .env.example .env
# edit locally if needed; never commit real keys
```

## How To Run CHF

1. Install dependencies and create a local `.env` file for any provider keys you want to use.
2. Check API readiness:

```bash
python3 scripts/probe_api_readiness.py --config configs/run_config.yaml
```

3. Run the full pipeline:

```bash
./run_all.sh
```

4. Or run one stage at a time:

```bash
python3 main.py universe --config configs/run_config.yaml
python3 scripts/verify_universe_run.py --config configs/run_config.yaml
```

Replace `universe` with `market`, `onchain`, `features`, `labels`, `model`, `portfolio`, or `backtest` and run the matching verifier.

5. Launch the dashboard after local data has been generated:

```bash
streamlit run app/dashboard.py
```

Beginner guides:

- [User Guide](docs/USER_GUIDE.md)
- [API Keys And Data Sources](docs/API_KEYS_AND_DATA_SOURCES.md)
- [Dashboard Guide](docs/DASHBOARD_GUIDE.md)

Run syntax validation:

```bash
python3 -m py_compile main.py agents/*.py providers/*.py features/*.py models/*.py pipelines/*.py scripts/*.py
```

Run targeted research-integrity tests:

```bash
python3 -m pytest \
  tests/test_alpha_research_agent.py \
  tests/test_model_agent_research_mode.py \
  tests/test_portfolio_agent_research_mode.py \
  tests/test_backtest_agent_research_mode.py \
  tests/test_universe_agent_research_mode.py \
  tests/test_market_data_agent_research_mode.py \
  tests/test_onchain_agent_research_mode.py \
  tests/test_feature_agent_research_mode.py \
  tests/test_label_agent_research_mode.py \
  -q
```

Probe API readiness:

```bash
python3 scripts/probe_api_readiness.py --config configs/run_config.yaml
```

Audit local pipeline inputs:

```bash
python3 scripts/audit_pipeline_inputs.py --config configs/run_config.yaml
```

See [docs/REPRODUCIBILITY_COMMANDS.md](docs/REPRODUCIBILITY_COMMANDS.md) for complete reproduction notes.

## Data And API Notes

- Generated artifacts are not committed.
- `data/` is ignored by Git.
- Local runs create Parquet, JSON, and Markdown outputs under `data/`.
- CoinMetrics Community and DeFiLlama may work without keys.
- CoinMarketCap historical listings for a 3-year point-in-time universe were blocked by current plan access during this study.
- `.env` must never be committed.
- The repository should use `.env.example` for non-secret local setup guidance only.

## Final Reports

- [Research Results Summary](docs/RESEARCH_RESULTS_SUMMARY.md)
- [Alpha Findings Report](docs/ALPHA_FINDINGS_REPORT.md)
- [Alpha Backtest Verification Report](docs/ALPHA_BACKTEST_VERIFICATION_REPORT.md)
- [Alpha Signal Search Report](docs/ALPHA_SIGNAL_SEARCH_REPORT.md)
- [Benchmark Verification](docs/BENCHMARK_VERIFICATION.md)
- [Final Reviewer Packet](docs/FINAL_REVIEWER_PACKET.md)
- [Reproducibility Checklist](docs/REPRODUCIBILITY_CHECKLIST.md)
- [Artifact Manifest](docs/ARTIFACT_MANIFEST.md)
- [Final Release Audit](docs/FINAL_RELEASE_AUDIT.md)
- [Limitations And Next Steps](docs/LIMITATIONS_AND_NEXT_STEPS.md)
- [Reproducibility Commands](docs/REPRODUCIBILITY_COMMANDS.md)
- [API Data Readiness Audit](docs/API_DATA_READINESS_AUDIT.md)
- [CMC Historical Access Limitation](docs/CMC_HISTORICAL_ACCESS_LIMITATION.md)
- [Pipeline Run Report](docs/PIPELINE_RUN_REPORT.md)

## Limitations

- The current production universe is a latest-survivor baseline, not a full point-in-time historical universe.
- CoinMarketCap 3-year historical listings access was blocked by the current API plan.
- On-chain coverage is sparse relative to market coverage.
- There is no real-time execution engine.
- No verified alpha found under tested configurations.
- Research and education only; not financial advice.

## License

MIT License.

For research and educational purposes only. Not financial advice.
