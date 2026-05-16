# Final Reviewer Packet

## A. Project Question

Can market and on-chain features produce crypto alpha after transaction costs, realistic portfolio construction, and benchmark comparison?

## B. What Was Built

CHF is a reproducible crypto alpha research pipeline:

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

Briefly:

- UniverseAgent builds the eligible crypto universe.
- MarketDataAgent collects and validates daily market OHLCV data.
- OnChainAgent collects CoinMetrics, DeFiLlama, and optional provider metrics where available.
- FeatureAgent creates leakage-safe market and on-chain features.
- LabelAgent creates exact forward calendar labels.
- ModelAgent performs leakage-safe walk-forward signal screening.
- AlphaResearchAgent expands signal search but remains signal-only.
- PortfolioAgent converts prediction-only files into deterministic allocations.
- BacktestAgent is the final alpha authority and verifies or rejects alpha after costs and benchmarks.

## C. How To Review Results Quickly

Recommended reading order:

1. [README.md](../README.md)
2. [Research Results Summary](RESEARCH_RESULTS_SUMMARY.md)
3. [Alpha Findings Report](ALPHA_FINDINGS_REPORT.md)
4. [Alpha Backtest Verification Report](ALPHA_BACKTEST_VERIFICATION_REPORT.md)
5. [Benchmark Verification](BENCHMARK_VERIFICATION.md)
6. [Limitations And Next Steps](LIMITATIONS_AND_NEXT_STEPS.md)
7. [User Guide](USER_GUIDE.md)

## D. Final Result

No verified alpha found under tested configurations.

Candidate signals were found, but BacktestAgent did not verify alpha against BTC, ETH, BTC/ETH 50-50, and equal-weight universe benchmarks.

## E. Strongest Candidate

`linear_ridge / market_only / raw_forward_return / 30d`

- Return: `147.36%`
- CAGR: `31.84%`
- Sharpe: `0.7521`
- Max drawdown: `-59.40%`
- Beat ETH and equal-weight universe.
- Did not beat BTC or BTC/ETH 50-50.
- `alpha_verified=false`

## F. Why This Is Still A Good Research Result

CHF found statistically promising signals, then subjected them to deterministic portfolio construction, transaction costs, benchmark sanity checks, and candidate-by-candidate backtesting. The backtest layer rejected unsupported alpha claims.

That is the right outcome for a research system: it avoids manufacturing fake alpha, documents what worked and failed, and leaves a reproducible trail for future improvements.

## G. How To Reproduce Or Inspect

- [User Guide](USER_GUIDE.md)
- [API Keys And Data Sources](API_KEYS_AND_DATA_SOURCES.md)
- [Reproducibility Commands](REPRODUCIBILITY_COMMANDS.md)

Generated outputs are local under `data/` and are ignored by Git. A fresh checkout needs local data generation or archived artifacts to reproduce the exact report files.

## H. Limitations

- Current production universe is a latest-survivor baseline.
- CoinMarketCap 3-year historical listings access was blocked by the current API plan.
- Full point-in-time historical universe membership is not yet available.
- On-chain coverage is sparse relative to market coverage.
- There is no live execution engine.
- Results are for research and education only, not financial advice.
