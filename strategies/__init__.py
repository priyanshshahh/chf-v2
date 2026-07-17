"""CHF multi-strategy sleeve layer.

Deterministic (NO LLM) signal -> target-weight generators. Each sleeve emits
an allocation parquet compatible with the papertrade engine:
columns >= [date_ts, execution_date, symbol, weight], one row-set per
rebalance date, weights >= 0 summing <= 1.0 (remainder is cash).

Sleeves:
    trend_sleeve        -- parsimonious 3-window trend voting (ANB style)
    xsmom_sleeve        -- BTC-relative cross-sectional momentum w/ regime gate
    carry_sleeve        -- funding-rate basis capture proxy (spot leg only)
    technical_ensemble  -- 5-family technical ensemble (virattt formulas)

Supporting modules:
    regime      -- daily market regime classifier
    allocator   -- pod-shop capital allocator (PROPOSAL artifact only)
    run_sleeves -- CLI that generates all sleeve weights + papertrade books
"""
