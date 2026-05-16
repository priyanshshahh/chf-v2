# Benchmark Verification

## A. Why BTC 305.50% Is Not The Same As "Last 5 Years"

CHF benchmarks use the same start and end dates as each candidate strategy backtest. Public websites often show a trailing five-year return measured from the current day, with their own exchange, close-time, timezone, and data-vendor conventions. That is a different window.

The CHF BTC benchmark is fair because it is measured over the exact same dates as the candidate strategies being tested. In the candidate-by-candidate verification runs, the benchmark window was:

```text
2022-12-15 00:00:00 UTC through 2026-03-24 00:00:00 UTC
```

The reported BTC benchmark return of `305.50%` is therefore the BTC buy-and-hold return over CHF's tested backtest window after the BacktestAgent benchmark transaction-cost convention, not a public trailing five-year chart return.

## B. Exact Benchmark Windows

| Candidate | Backtest Start | Backtest End | BTC Return | ETH Return | BTC/ETH 50-50 Return | Equal-weight Universe Return |
|---|---|---|---:|---:|---:|---:|
| lightgbm / market_only / raw_forward_return / 14d | 2022-12-15 | 2026-03-24 | 305.50% | 69.85% | 178.04% | 30.39% |
| linear_ridge / market_only / raw_forward_return / 30d | 2022-12-15 | 2026-03-24 | 305.50% | 69.85% | 178.04% | 30.39% |
| random_forest / market_only / raw_forward_return / 14d | 2022-12-15 | 2026-03-24 | 305.50% | 69.85% | 178.04% | 30.39% |

All three individually tested candidate backtests used the same benchmark window.

## C. BTC Manual Price Check

The strongest candidate was `linear_ridge / market_only / raw_forward_return / 30d`. Its BTC benchmark was checked directly from `data/raw/market/market_ohlcv.parquet`.

| Item | Value |
|---|---:|
| Start date | 2022-12-15 00:00:00 UTC |
| Start BTC close | 17,359.21 |
| End date | 2026-03-24 00:00:00 UTC |
| End BTC close | 70,532.10 |
| Manual raw return | 306.31% |
| Manual return after 20 bps initial benchmark cost | 305.50% |
| BacktestAgent BTC return | 305.50% |
| Difference after cost convention | 0.000000% |

Source used for BTC closes in the local market file: `cryptocompare`.

## D. Formula

Raw close-to-close return:

```text
total_return = end_close / start_close - 1
```

For the BTC manual check:

```text
raw_total_return = 70,532.10 / 17,359.21 - 1 = 306.31%
```

BacktestAgent also applies the configured benchmark transaction-cost convention. With a 20 bps initial cost:

```text
net_benchmark_return = (1 - 20 / 10000) * (1 + raw_total_return) - 1
net_benchmark_return = 305.50%
```

## E. Conclusion

BTC `305.50%` is the BTC buy-and-hold benchmark return over CHF's tested candidate backtest window, after the BacktestAgent benchmark transaction-cost convention. It is not a trailing five-year return from a public chart.

Small differences versus websites can occur due to exchange, close-time, timezone, transaction-cost assumptions, or data-vendor conventions. A large difference should be investigated by checking the exact dates, symbol, close prices, and whether costs are included.
