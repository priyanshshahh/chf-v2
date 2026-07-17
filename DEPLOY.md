# Deploying the CHF Dashboard (free, read-only demo)

Staged target: **Streamlit Community Cloud** (free tier). The deployment is a
read-only demo — it needs **no API keys, no secrets, and none of the local
data zips**.

## What is staged

| Piece | Purpose |
|---|---|
| `streamlit_app.py` (repo root) | Cloud entry point. Sets `CHF_DASHBOARD_READ_ONLY=1`, generates synthetic demo artifacts on first boot (same as `python main.py demo` — real `data/` outputs are gitignored and absent from a fresh checkout), then runs `app/dashboard.py`. |
| `app/dashboard.py` read-only gate | With `CHF_DASHBOARD_READ_ONLY=1`, the Pipeline Control Center is display-only and `run_command()` refuses to execute anything — per the security note in the module docstring. |
| `requirements.txt` (repo root) | Streamlit Community Cloud installs from the repo-root `requirements.txt` automatically. |

Verified locally: `streamlit run streamlit_app.py` serves HTTP 200 and
`/_stcore/health` = `ok`; demo-artifact generation into a clean root works
offline with no `.env`.

## Owner steps (one time, ~5 minutes)

1. Push `main` to GitHub (`github.com/priyanshshahh/chf-v2`). Nothing secret is
   required — `.env` stays local (verified never committed).
2. Go to **https://share.streamlit.io** and sign in with the GitHub account
   that owns the repo.
3. Click **Create app** → **Deploy a public app from GitHub**.
4. Fill in:
   - **Repository:** `priyanshshahh/chf-v2`
   - **Branch:** `main`
   - **Main file path:** `streamlit_app.py`
5. Open **Advanced settings** and set **Python version: 3.11** (the project
   pins `requires-python >= 3.11`; vectorbt/numba wheels are reliable on 3.11).
6. **Secrets:** leave empty. The demo needs none. Do NOT paste the local
   `.env` keys — nothing in the dashboard uses them.
7. Click **Deploy**. First build takes a while (the root `requirements.txt`
   installs the full research stack — vectorbt, catboost, xgboost, shap,
   mlflow). First page load also spends a few seconds generating demo
   artifacts; later loads are instant.
8. Copy the public URL (e.g. `https://<app-name>.streamlit.app`) into the
   README "Live demo" section.

## Honest constraints

- **Heavy dependency set.** Streamlit Community Cloud only reads the repo-root
  `requirements.txt`, which carries the entire research stack (~1 GB of
  wheels). It generally fits the free tier, but if the build fails on memory
  or on a numba/vectorbt resolution error, the fallback below avoids slimming
  the root requirements (which the pipeline documentation depends on).
- **Demo data is synthetic.** A fresh checkout has no `data/` outputs (they
  are gitignored by design), so the cloud app shows the `main.py demo`
  synthetic artifacts, clearly stamped `snapshot_id=demo`. The real research
  verdict pages (frozen release, `alpha_verified=false` framing, docs) render
  from committed docs and are accurate. The big CMC zips are NOT needed.
- **Free tier sleeps** after inactivity; first visit after sleep takes ~1 min
  to wake.

## Fallback: Hugging Face Spaces (Docker), if the Cloud build fails

1. Create a Space → SDK: **Docker** → link or push this repo.
2. Add a `Dockerfile` at the root:

   ```dockerfile
   FROM python:3.11-slim
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   COPY . .
   EXPOSE 7860
   CMD ["streamlit", "run", "streamlit_app.py", "--server.port", "7860", "--server.address", "0.0.0.0"]
   ```

3. No secrets/variables needed. Same read-only demo behavior.

Render.com works identically (Web Service → Docker → free instance), but its
free tier also sleeps and builds are slower; HF Spaces is the better fallback.
