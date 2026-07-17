# PROJECT-NOTES — hygiene / verification / deploy-staging pass (2026-07-06)

Scope of this pass: security audit, repo hygiene, test-claim verification,
offline demo verification, Streamlit Cloud deploy staging, README accuracy.
No pipeline restructuring, no modeling-logic changes, nothing pushed.

## 1. Security check (RESULT: CLEAN)

`.env` at repo root holds 4 live keys: `COINMARKETCAP_API_KEY`,
`ETHERSCAN_API_KEY`, `COINGECKO_API_KEY`, `CMC_API_KEY` (values never printed
or copied anywhere during this audit).

Commands and findings:

- `git log --all --diff-filter=A -- .env` → **empty**. `.env` was never
  committed in any of the repo's 16 commits (`git rev-list --all --count` = 16).
  Only `.env.example` is tracked.
- `unzip -l cmc_all_data.zip` (1122 files) and `unzip -l cmc_for_professor.zip`
  (20 files) → **no `.env`, no key/credential files**. The only "key" grep
  matches are `keyless_data_api_listings/` directory names.
- Python scan: read the 4 key values from `.env` locally, byte-searched
  (a) all 209 git-tracked files, (b) every commit in history via
  `git rev-list --all | xargs git grep --fixed-strings <value>`, and
  (c) every member of both zips (including the 86 MB CSV, streamed in
  chunks) → **zero hits everywhere**.
- `.gitignore` already blocks `.env`, `.env.*` (except `.env.example`),
  `*.pem`, `*.key`, etc. A gitleaks config and secret-scan pre-commit hook
  (`make hooks`) exist.

Conclusion: no key material has ever entered git history, the tracked tree,
or the distributable zips. No history rewrite needed for secrets.

## 2. Repo hygiene

- `.venv/` (single tree containing both python3.11 and python3.13 binaries;
  `python` resolves to 3.11.0) → already gitignored, **not tracked**. No
  `git rm --cached` needed.
- `git ls-files` + stat: **no venvs, no mlruns, no zips, no .docx, no
  node_modules tracked**. Largest tracked file is 81 KB
  (`agents/market_data_agent.py`); second is `docs/architecture.png` (77 KB).
  **No history rewrite recommended — history is already lean.**
- Gap found: `cmc_all_data.zip` (80 MB), `cmc_for_professor.zip` (34 MB),
  `cmc_all_data/`, `cmc_complete/` were untracked but **not** gitignored — a
  stray `git add .` would stage ~114 MB. **Fix: added all four to
  `.gitignore`** under the existing CMC bulk-data section. Zips left on disk
  untouched.

## 3. Test-claim verification (README said "600+ passing tests")

- Environment: repo's own `.venv` (Python 3.11.0, pytest 9.0.3), repo's
  documented command (`make test` → `python -m pytest tests/`).
- Collected: **628 test cases across 40 test files**.
- Result (`pytest tests/ -q`, 338 s):
  **625 passed / 1 failed / 2 skipped**.
- The 1 failure: `tests/test_pipeline_integration.py::test_pipeline_agents_run_end_to_end_from_market_data`.
  Root cause (verified, incl. after clearing stale bytecode caches via
  `PYTHONPYCACHEPREFIX`): the live `configs/run_config.yaml` now sets
  `research_mode: true`, and `FeatureAgentV1.prepare()` (agents/feature_agent.py:196)
  then requires canonical inputs (`market_ohlcv.parquet`, `onchain_wide.parquet`,
  `onchain_observations.parquet`, `universe_monthly.parquet` + manifests). The
  test fixture still writes only legacy per-symbol `{SYM}_ohlcv.parquet` files.
  This is **test/config drift**, not a modeling bug; left unfixed on purpose
  (fixing means changing either the fixture's pipeline contract or the config —
  owner's call). README updated to the measured numbers.

## 4. Offline demo verification

- `./scripts/demo.sh` → **exit 0**, no keys, no network. Produced:
  research verdict print (alpha_verified=false), papertrade cycle across 13
  books, NAV reconciliation, monitoring reports (alerts on stale research
  data — documented as expected), monthly letter
  `artifacts/letters/2026-07_letter.md/.json`.
- Streamlit dashboard: `streamlit run app/dashboard.py --server.port 8599
  --server.headless true` → HTTP **200** on `/`, `/_stcore/health` = `ok`.

## 5. Deploy staging (Streamlit Community Cloud, read-only, no secrets)

Changes:

- **NEW `streamlit_app.py`** (repo root): cloud entry point. Sets
  `CHF_DASHBOARD_READ_ONLY=1`, generates synthetic demo artifacts on first
  boot when `data/backtests/backtest_summary.parquet` is absent (fresh cloud
  checkout — `data/` is gitignored), then runs `app/dashboard.py` via runpy.
- **`app/dashboard.py`**: added `READ_ONLY` env flag
  (`CHF_DASHBOARD_READ_ONLY`); when set, the Pipeline Control Center is
  display-only and `run_command()` refuses execution — implements the
  module's own "disable before public deployment" security note. No other
  dashboard behavior changed.
- **NEW `DEPLOY.md`**: exact owner steps (share.streamlit.io → Create app →
  repo `priyanshshahh/chf-v2`, branch `main`, main file `streamlit_app.py`,
  Python 3.11, secrets EMPTY), honest constraints (heavy root
  requirements.txt, synthetic demo data, free-tier sleep), and a Hugging Face
  Spaces Docker fallback.

Verification:

- `generate_demo_artifacts(load_config())` into a clean temp root → works
  offline, writes market/features/labels/predictions/allocations/backtests.
- `streamlit run streamlit_app.py` (port 8598) → HTTP 200, health `ok`.

## 6. README changes

- Test claim corrected: "600+ passing tests" → "628-case pytest suite across
  40 test files (measured 2026-07-06: 625 passed, 2 skipped, 1 known failure
  ...)" with the failure honestly characterized.
- Added "Live demo (Streamlit Community Cloud)" subsection under Dashboard
  and Automation, pointing at `streamlit_app.py` and `DEPLOY.md`.
- `alpha_verified=false` framing untouched.

## Files changed in this pass

- `.gitignore` — added cmc zip/dir ignore rules
- `app/dashboard.py` — read-only demo gate (3 small edits)
- `streamlit_app.py` — new cloud entry point
- `DEPLOY.md` — new
- `README.md` — test count fix + Live demo section
- `docs/PROJECT-NOTES.md` — this file

Not done / owner decisions:

- Fix or retire the drifted integration test (see §3).
- Push to GitHub + click through the Streamlit Cloud deploy (DEPLOY.md §Owner
  steps); nothing was pushed from this pass.
