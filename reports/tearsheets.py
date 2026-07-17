"""QuantStats HTML tear sheets for backtest strategies and paper-trading books.

Reporting only — this script never feeds back into alpha verification.
`BacktestAgent` remains the sole alpha authority (see docs/NEXT_STEPS.md item [7]).

Inputs (read-only):
  - data/backtests/equity_curves.parquet
      Long format: one row per (date_ts, strategy_name) with `net_return` and
      `benchmark_type` in {"strategy", "benchmark"}.
  - data/papertrade/*/equity.parquet
      Per-book daily NAV rows with a `daily_return` column.

Outputs:
  - artifacts/tearsheets/<name>.html            (backtest strategies/benchmarks)
  - artifacts/tearsheets/papertrade_<book>.html (paper-trading books)

Guards:
  - A series is only rendered when it has at least MIN_POINTS (30) daily points.
  - Paper-trading books need more than one equity row (a single row has no
    return history).
  - Degenerate all-zero return series (e.g. the flat BTC/ETH/cash benchmark
    curves in the current frozen run) are skipped — QuantStats divides by the
    return volatility and produces meaningless/NaN sheets for them.

Benchmark column: `equal_weight_universe` when present and non-degenerate,
else the `BTC` benchmark curve, else no benchmark.

Idempotent: re-running overwrites the same output files.

Usage:
    .venv/bin/python reports/tearsheets.py
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "tearsheets"
EQUITY_CURVES_PATH = PROJECT_ROOT / "data" / "backtests" / "equity_curves.parquet"
PAPERTRADE_DIR = PROJECT_ROOT / "data" / "papertrade"

MIN_POINTS = 30
PERIODS_PER_YEAR = 365  # crypto trades every calendar day; matches BacktestAgent annualization_days


def _load_backtest_series(path: Path) -> Dict[str, "pd.Series"]:
    """Return {strategy_name: tz-naive daily net-return series} from equity_curves.parquet."""
    import pandas as pd

    df = pd.read_parquet(path)
    required = {"date_ts", "strategy_name", "net_return"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing required columns: {sorted(missing)}")
    out: Dict[str, pd.Series] = {}
    for name, grp in df.groupby("strategy_name"):
        idx = pd.to_datetime(grp["date_ts"], utc=True).dt.tz_localize(None)
        series = pd.Series(
            pd.to_numeric(grp["net_return"], errors="coerce").fillna(0.0).values,
            index=idx,
            name=str(name),
        ).sort_index()
        out[str(name)] = series
    return out


def _load_papertrade_series(root: Path) -> Dict[str, Tuple["pd.Series", str]]:
    """Return {book_name: (return series, skip_reason)}; skip_reason == "" when usable."""
    import pandas as pd

    out: Dict[str, Tuple[pd.Series, str]] = {}
    if not root.is_dir():
        return out
    for book_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        book = book_dir.name
        eq_path = book_dir / "equity.parquet"
        if not eq_path.exists():
            out[book] = (pd.Series(dtype=float), "no equity.parquet (book has no filled equity history yet)")
            continue
        try:
            df = pd.read_parquet(eq_path)
        except Exception as exc:  # noqa: BLE001 - report and continue
            out[book] = (pd.Series(dtype=float), f"unreadable equity.parquet ({exc})")
            continue
        if "daily_return" not in df.columns or "date" not in df.columns:
            out[book] = (pd.Series(dtype=float), "missing date/daily_return columns")
            continue
        if len(df) <= 1:
            out[book] = (pd.Series(dtype=float), f"only {len(df)} equity row(s); need >1")
            continue
        idx = pd.to_datetime(df["date"])
        series = pd.Series(
            pd.to_numeric(df["daily_return"], errors="coerce").fillna(0.0).values,
            index=idx,
            name=book,
        ).sort_index()
        out[book] = (series, "")
    return out


def _pick_benchmark(series_by_name: Dict[str, "pd.Series"]) -> Optional[str]:
    """Prefer the equal-weight universe benchmark, fall back to BTC; require non-degenerate."""
    for candidate in ("equal_weight_universe", "BTC"):
        s = series_by_name.get(candidate)
        if s is not None and len(s) >= MIN_POINTS and float(s.std()) > 0.0:
            return candidate
    return None


def _render(returns: "pd.Series", benchmark: Optional["pd.Series"], title: str, out_path: Path) -> None:
    import quantstats as qs

    kwargs = dict(
        output=str(out_path),
        title=title,
        download_filename=out_path.name,
        periods_per_year=PERIODS_PER_YEAR,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if benchmark is not None:
            qs.reports.html(returns, benchmark=benchmark, benchmark_title=str(benchmark.name), **kwargs)
        else:
            qs.reports.html(returns, **kwargs)


def main() -> int:
    try:
        import pandas as pd  # noqa: F401
    except ImportError as exc:
        print(f"[tearsheets] FATAL: pandas unavailable: {exc}")
        return 1
    try:
        import matplotlib

        matplotlib.use("Agg")  # headless rendering
        import quantstats  # noqa: F401
    except ImportError as exc:
        print(f"[tearsheets] FATAL: quantstats/matplotlib unavailable: {exc}")
        print("[tearsheets] install with: .venv/bin/python -m pip install 'quantstats>=0.0.62'")
        return 1

    generated: List[str] = []
    skipped: List[str] = []

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ backtests
    if EQUITY_CURVES_PATH.exists():
        try:
            backtest_series = _load_backtest_series(EQUITY_CURVES_PATH)
        except Exception as exc:  # noqa: BLE001
            print(f"[tearsheets] ERROR reading {EQUITY_CURVES_PATH}: {exc}")
            backtest_series = {}
        bench_name = _pick_benchmark(backtest_series)
        bench = backtest_series.get(bench_name) if bench_name else None
        if bench_name:
            print(f"[tearsheets] benchmark column: {bench_name}")
        else:
            print("[tearsheets] no usable benchmark column found (rendering without benchmark)")
        for name in sorted(backtest_series):
            series = backtest_series[name]
            if len(series) < MIN_POINTS:
                skipped.append(f"{name}: only {len(series)} daily points (<{MIN_POINTS})")
                continue
            if float(series.std()) == 0.0:
                skipped.append(f"{name}: degenerate all-constant return series (flat curve)")
                continue
            out_path = OUTPUT_DIR / f"{name}.html"
            use_bench = bench if (bench is not None and name != bench_name) else None
            try:
                _render(series, use_bench, f"CHF backtest — {name}", out_path)
                generated.append(str(out_path))
            except Exception as exc:  # noqa: BLE001 - reporting must not hard-fail the batch
                skipped.append(f"{name}: quantstats render failed ({exc})")
    else:
        skipped.append(f"backtests: {EQUITY_CURVES_PATH} missing")

    # ---------------------------------------------------------------- papertrade
    pt = _load_papertrade_series(PAPERTRADE_DIR)
    if not pt:
        skipped.append(f"papertrade: no */equity.parquet under {PAPERTRADE_DIR}")
    for book in sorted(pt):
        series, reason = pt[book]
        if reason:
            skipped.append(f"papertrade/{book}: {reason}")
            continue
        if len(series) < MIN_POINTS:
            skipped.append(f"papertrade/{book}: only {len(series)} daily points (<{MIN_POINTS})")
            continue
        if float(series.std()) == 0.0:
            skipped.append(f"papertrade/{book}: degenerate all-constant return series")
            continue
        out_path = OUTPUT_DIR / f"papertrade_{book}.html"
        try:
            _render(series, None, f"CHF paper trading — {book}", out_path)
            generated.append(str(out_path))
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"papertrade/{book}: quantstats render failed ({exc})")

    # ------------------------------------------------------------------- summary
    print(f"\n[tearsheets] generated {len(generated)} tear sheet(s):")
    for path in generated:
        print(f"  + {path}")
    print(f"[tearsheets] skipped {len(skipped)}:")
    for note in skipped:
        print(f"  - {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
