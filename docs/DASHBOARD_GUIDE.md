# Dashboard Guide

CHF includes a Streamlit dashboard for inspecting locally generated research artifacts. The dashboard is a viewer for local outputs; it does not create verified alpha and it is not an execution system.

## Before Running The Dashboard

Run at least the relevant pipeline stages first so local `data/` artifacts exist.

For a full local run:

```bash
./run_all.sh
```

For a quick API/data status check:

```bash
python3 scripts/probe_api_readiness.py --config configs/run_config.yaml
python3 scripts/audit_pipeline_inputs.py --config configs/run_config.yaml
```

The dashboard expects generated local files under paths such as:

```text
data/raw/
data/features/
data/labels/
data/predictions/
data/allocations/
data/backtests/
data/research/
```

`data/` is ignored by Git, so a fresh clone will not include these outputs.

## Run The Dashboard

Use Streamlit directly:

```bash
streamlit run app/dashboard.py
```

or use the helper script:

```bash
./run_dashboard.sh
```

Then open:

```text
http://localhost:8501
```

## What You Should See

Depending on which artifacts exist locally, the dashboard may show:

- universe and market coverage,
- feature and label summaries,
- model signal diagnostics,
- portfolio allocation outputs,
- backtest equity curves and summaries,
- final research reports.

If a panel is empty, first check whether the corresponding pipeline stage has been run and verified.

## Troubleshooting

If the dashboard does not start:

```bash
python3 -m pip install -r requirements.txt
python3 -m py_compile app/dashboard.py
```

If the dashboard starts but data is missing:

```bash
python3 scripts/audit_pipeline_inputs.py --config configs/run_config.yaml
```

If a pipeline stage failed, run its verifier directly and fix that stage before relying on dashboard views.

Important research rule: dashboard displays are not alpha verification. BacktestAgent is the only stage allowed to verify alpha.
