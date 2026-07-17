# Developer setup

## The `.venv/bin/pip` breakage (read this first)

This project's virtualenv has a **broken `pip` shim**: `.venv/bin/pip` points at
a `python3.13` library directory, while the venv interpreter is **Python 3.11**.
Running `.venv/bin/pip` therefore fails (or, worse, resolves against the wrong
site-packages).

**Do not try to rebuild or "fix" the venv.** Just avoid the `pip` shim and drive
pip through the interpreter's module form, which always uses the correct 3.11
environment:

```bash
# CORRECT — always use the interpreter + -m pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install <package>
.venv/bin/python -m pip list

# BROKEN — do NOT use; targets a python3.13 lib dir
.venv/bin/pip install -r requirements.txt
```

Rule of thumb: **whenever you would type `.venv/bin/pip ...`, type
`.venv/bin/python -m pip ...` instead.**

## Running things

```bash
# tests
.venv/bin/python -m pytest tests/ -q

# the ops-layer tests specifically
.venv/bin/python -m pytest tests/test_ops_layer.py -q

# transaction-cost model demo
.venv/bin/python -m portfolio.cost_model --demo

# notifier self-check (logs only unless SMTP_*/WEBHOOK_URL are set)
.venv/bin/python -m monitoring.notifier

# the scheduler (long-running; Ctrl+C to stop)
.venv/bin/python -m jobs.scheduler
```

## Deployment

For running CHF continuously (Docker / systemd / supervised loop / pm2),
env & secrets setup, and log locations, see [`deploy/README.md`](../deploy/README.md).
The Docker image builds a clean `python:3.11-slim` environment and uses
`python -m pip`, so it is unaffected by the venv-pip breakage above.
