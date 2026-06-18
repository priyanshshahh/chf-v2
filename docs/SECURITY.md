# Security — publishing CHF to GitHub

This project is designed so **source code** is public while **secrets and bulk data** stay on your machine.

## What must never be committed

| Item | Where it lives | Protection |
|------|----------------|------------|
| API keys | `.env` | gitignored + pre-commit scan |
| Pipeline data | `data/` | gitignored |
| Model outputs | `artifacts/`, `mlruns/` | gitignored |
| CMC bulk extracts | `coinmarketcap_data/*`, `coinmarketcap_extract/raw_daily_json/` | gitignored |
| Credential files | `*.pem`, `*.key`, `credentials.json` | gitignored + pre-commit scan |

Only **`.env.example`** belongs in git — keep every key line commented or empty.

## Before your first push

```bash
# 1. Install the local secret-scan hook (runs on every commit)
bash scripts/install_git_hooks.sh

# 2. Confirm .env is not tracked
git ls-files .env   # should print nothing

# 3. Scan all tracked files once
python3 scripts/check_secrets.py   # no staged files → OK
git ls-files -z | xargs -0 git add -N  # optional dry-run helper
```

If you ever pasted a real key into a file by mistake, **rotate/revoke that key** at the provider (CoinMarketCap, CoinGecko, etc.) even if the commit never pushed.

## Automated checks on GitHub

The workflow [`.github/workflows/secret-scan.yml`](../.github/workflows/secret-scan.yml) runs on every push and pull request:

- **gitleaks** — industry-standard secret scanner (config: `.gitleaks.toml`)
- **Python checker** — same rules as the local pre-commit hook

Failed scans block merging until secrets are removed from history.

## Protecting the code itself

GitHub cannot stop someone from **copying public source code**. Choose the visibility that matches your intent:

| Goal | Recommendation |
|------|----------------|
| Open research / portfolio project | Public repo + MIT license (see `pyproject.toml`) |
| Keep implementation private | **Private repository** on GitHub |
| Allow view but restrict reuse | Public repo + proprietary license (add a `LICENSE` file) |

Additional GitHub settings (repo → Settings → General / Security):

- Enable **Private vulnerability reporting** (Security tab)
- Enable **Dependabot alerts** for dependency CVEs
- For team repos: require pull-request reviews and the secret-scan workflow on `main`

## If a secret was committed

1. Revoke/rotate the exposed key immediately at the provider.
2. Remove it from git history (e.g. `git filter-repo` or BFG Repo-Cleaner) — a normal delete commit is **not** enough; history retains it.
3. Force-push only after history rewrite, then confirm GitHub secret scanning shows clean.

For help: [GitHub — Removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)
