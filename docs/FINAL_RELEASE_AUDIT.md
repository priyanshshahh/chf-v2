# Final Release Audit

## Repository State

- Branch: `main`
- Current commit hash: `9894d7b6`
- Working tree status at start of Phase 11: clean and synced with `origin/main`
- Working tree status during audit: documentation-only changes for Phase 11

## Validation Commands Run

Python compile check:

```bash
python3 -m py_compile main.py agents/*.py providers/*.py features/*.py models/*.py pipelines/*.py scripts/*.py
```

Targeted pytest check:

```bash
python3 -m pytest tests/test_alpha_research_agent.py tests/test_model_agent_research_mode.py tests/test_backtest_agent_research_mode.py -q
```

Markdown/link sanity check:

```text
Local Python check verifies:
- newly added Markdown files exist,
- README/local docs links point to existing files,
- no obvious placeholder-marker text was introduced in the Phase 11 docs.
```

## Validation Results

| Check | Result |
|---|---|
| Python compile | PASS |
| Targeted pytest | PASS, `54 passed` |
| Markdown/link sanity | PASS after `docs/FINAL_RELEASE_AUDIT.md` was created |

## Phase 11 Files Changed

- `README.md`
- `docs/REPRODUCIBILITY_CHECKLIST.md`
- `docs/ARTIFACT_MANIFEST.md`
- `docs/FINAL_RELEASE_AUDIT.md`

## Known Limitations

- No verified alpha found under tested configurations.
- Current production universe is a latest-survivor baseline.
- CoinMarketCap 3-year historical listings access was blocked by current plan access.
- On-chain coverage is sparse relative to market coverage.
- Generated `data/` artifacts are ignored by Git and may not exist in a fresh clone.
- Public trailing "last 5 years" BTC charts will not exactly match CHF benchmark values because CHF uses each candidate's exact backtest window and BacktestAgent benchmark cost convention.

## Non-Modification Statement

Phase 11 did not change:

- source logic,
- model or backtest calculations,
- generated outputs,
- `.env` files or secrets,
- data files,
- model artifacts,
- benchmark returns,
- strategy returns,
- dates,
- final research claims.

No git tag was created in Phase 11.
