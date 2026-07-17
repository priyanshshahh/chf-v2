# Running Project CHF continuously

This directory contains everything needed to run the CHF paper fund as a live,
monitored, supervised process. The unit of deployment is **`jobs/scheduler.py`**
(a `BlockingScheduler`) which shells out to:

- the pipeline stages — `python main.py <stage> --config configs/run_config.yaml`
- the operational monitors — `python -m monitoring.<name>`
- the daily data collectors — `python -m providers.<name>`

When a monitor reports an alert, or a scheduled job errors or is missed, the
scheduler dispatches a **deduplicated** notification through
`monitoring/notifier.py` (email + webhook; see "Secrets / env" below).

Pick **one** of the three run methods below.

---

## 1. Docker (recommended)

```bash
cp .env.example .env          # then edit: SMTP_*, WEBHOOK_URL, provider API keys
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml logs -f scheduler
```

- Image: `python:3.11-slim`, runs as non-root user `chf` (uid 10001).
- `data/` and `logs/` are bind-mounted to the host so outputs and scheduler
  logs survive container restarts.
- `restart: unless-stopped` restarts the container on crash / host reboot.
- An optional Postgres service is included but **commented out** — CHF runs on
  parquet/JSON by default.

Build/run the image directly (without compose):

```bash
docker build -f deploy/Dockerfile -t chf-scheduler .
docker run --env-file .env \
  -v "$PWD/data:/app/data" -v "$PWD/logs:/app/logs" \
  --restart unless-stopped chf-scheduler
```

## 2. systemd (Linux VM)

```bash
sudo cp deploy/chf-scheduler.service /etc/systemd/system/chf-scheduler.service
# edit User / WorkingDirectory / EnvironmentFile in the unit to match the host
sudo systemctl daemon-reload
sudo systemctl enable --now chf-scheduler
journalctl -u chf-scheduler -f
```

The unit calls `/opt/chf/.venv/bin/python -m jobs.scheduler` directly and
restarts on failure (`Restart=on-failure`, 15s backoff, crash-loop guard).

## 3. Supervised loop / pm2 (no Docker, no systemd)

Dependency-free supervisor with exponential backoff:

```bash
./deploy/run_supervised.sh
# or with an explicit interpreter:
PYTHON=/opt/chf/.venv/bin/python ./deploy/run_supervised.sh
```

It restarts the scheduler on crash (5s → 300s backoff, reset after a healthy
run), loads `.env`, and forwards Ctrl+C to the child. Supervisor events are
appended to `logs/scheduler/supervisor.log`.

If you already run **pm2**:

```bash
pm2 start deploy/run_supervised.sh --name chf-scheduler
# (or, to let pm2 own the restart policy:)
pm2 start .venv/bin/python --name chf-scheduler -- -m jobs.scheduler
pm2 save
```

---

## Secrets / env

Copy `.env.example` to `.env` and fill in what you have. **Secrets never live in
YAML or in git** — only in `.env` (or the systemd `EnvironmentFile`, `chmod 600`).

Notification channels (both optional; each degrades to *logged-only* if unset):

| Variable      | Purpose                                             |
|---------------|-----------------------------------------------------|
| `SMTP_HOST`   | SMTP server host (email channel)                    |
| `SMTP_PORT`   | SMTP port (default 587)                             |
| `SMTP_USER`   | SMTP username / login                               |
| `SMTP_PASS`   | SMTP password / app token                           |
| `SMTP_FROM`   | From address (falls back to `SMTP_USER`)            |
| `SMTP_TO`     | Comma-separated recipient list                      |
| `WEBHOOK_URL` | Slack / Discord / Mattermost / generic webhook URL  |

Non-secret notification behaviour (min severity, dedup TTL, subject prefix) is
configured in `configs/notifications.yaml`.

Provider API keys for the collectors go in the same `.env` (see `.env.example`).

## The venv-pip caveat (IMPORTANT)

In this repo **`.venv/bin/pip` is broken** — it targets a `python3.13` lib dir
while the interpreter is Python 3.11. Do **not** call `.venv/bin/pip`. Always
install and run through the interpreter's module form:

```bash
.venv/bin/python -m pip install -r requirements.txt   # correct
.venv/bin/pip install -r requirements.txt             # BROKEN — do not use
```

The Docker image sidesteps this entirely (it builds a clean `python:3.11-slim`
environment with `python -m pip`). See `docs/DEV_SETUP.md` for details.

## Log locations

| Path                                   | What                                            |
|----------------------------------------|-------------------------------------------------|
| `logs/scheduler/<ts>_<job>.log`        | per-run stdout/stderr of each job               |
| `logs/scheduler/heartbeat.json`        | per-job last start / success / exit code        |
| `logs/scheduler/failures.jsonl`        | job failures + `job_error` / `job_missed` events|
| `logs/scheduler/alert_state.json`      | last-sent alert hashes (notification dedup)     |
| `logs/scheduler/supervisor.log`        | `run_supervised.sh` restart events              |
| `data/reports/monitoring/<name>.json`  | each monitor's structured report (alert source) |

Container/service stdout also shows the live scheduler log:
`docker compose logs -f scheduler` or `journalctl -u chf-scheduler -f`.

## Notes

- **BacktestAgent is never scheduled** — it stays manual / research-validation
  only, by design.
- Collectors run at **05:00 UTC** (staggered by a few minutes), ahead of the
  05:45 data-quality gate and 06:00 market ingestion.
- The scheduler is safe to stop/start at any time; state is on disk.
