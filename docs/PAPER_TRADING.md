# CHF Paper Trading

CHF includes a paper-trading layer (`papertrade/`) that forward-tests every candidate strategy with virtual money. It is the ongoing out-of-sample proof for the research pipeline: each strategy runs as an independent virtual "book" against live market prices, with the same 20 bps transaction-cost model as BacktestAgent.

**Virtual only. Research validation. Not live trading, not financial advice.** No real-money execution exists anywhere in CHF.

## How it works

```text
allocations (research outputs) ──► papertrade engine ──► virtual books under data/papertrade/
                                        │
                                   live USD prices
                          (CMC quotes/latest → CoinGecko top-500 fallback)
```

- One **book per strategy** plus benchmark books (BTC, BTC/ETH 50-50), configured in the `papertrade:` section of `configs/run_config.yaml`.
- Daily run: mark-to-market every book; rebalance per book cadence (`weekly` / `daily` / `on_change`) toward the latest target weights from that strategy's allocation parquet.
- Fills are simulated at reference prices with configurable cost (default 20 bps, matching backtests).
- **Never fabricates prices**: if a held or targeted symbol cannot be priced, the book is skipped for the day with a logged reason. Prices that disagree >50% across providers are dropped (symbol-collision guard, e.g. TON = Toncoin vs Tokamak Network).
- **Idempotent per day**: re-running the same date overwrites that date's equity row and fills instead of duplicating.

## Running

```bash
python3 main.py papertrade --config configs/run_config.yaml           # all books, today
python3 main.py papertrade --as-of 2026-07-05 --books ridge_30d_top5  # subset
./run_scheduler.sh   # includes a daily papertrade job at 13:00 UTC
```

## Outputs (per book, under `data/papertrade/<book>/`)

| File | Contents |
|---|---|
| `state.json` | `cash`, `positions` (symbol→qty), `last_rebalance` |
| `equity.parquet` | `date`, `nav`, `cash`, `positions_value`, `daily_return`, `cum_return` |
| `fills.parquet` | `date`, `symbol`, `side`, `qty`, `price`, `notional`, `cost_usd`, `reason` |

Plus a run manifest at `data/papertrade/papertrade_manifest.json`.

Both dashboards display the books: the React site has a Paper Trading page, and the Streamlit console a Paper Trading tab.

## Brokers

- `simulator` (default): internal virtual book; no exchange, no keys.
- `alpaca`: routes the same target weights to Alpaca's **paper** endpoint (crypto). Requires `pip install alpaca-py` and `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` in `.env` (free paper keys from https://alpaca.markets). The broker is hard-wired to the paper base URL; real-money trading is not supported.

## Configuration

```yaml
papertrade:
  output_dir: "data/papertrade"
  broker: "simulator"
  books:
    - name: "ridge_30d_top5"
      weights_path: "data/allocations_candidate_linear_ridge_30d/allocations_top_5_equal_weight.parquet"
      rebalance: "weekly"
      cost_bps: 20
      starting_cash: 100000
```

## Interpreting results

Paper NAV curves are forward, out-of-sample evidence — the strongest kind. But they start from the day the books opened; judge strategies only after a meaningful accumulation period (months, multiple rebalances). `alpha_verified` remains BacktestAgent's call alone; paper trading corroborates or contradicts it over time.

Tests: `tests/test_papertrade.py` (fill math, costs, idempotency, persistence, rebalance cadence, Alpaca guards).
