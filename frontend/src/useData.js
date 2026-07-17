import { useEffect, useState } from "react";

// Files produced by frontend/scripts/build_data.py into /public/data/.
const FILES = {
  summary: "summary.json",
  equity: "equity.json",
  strategies: "strategies.json",
  costSweep: "cost_sweep.json",
  signals: "signals.json",
  // per-agent datasets
  universe: "universe.json",
  market: "market.json",
  onchain: "onchain.json",
  ablation: "ablation.json",
  features: "features.json",
  labels: "labels.json",
  models: "models.json",
  leaderboard: "leaderboard.json",
  portfolio: "portfolio.json",
  backtest: "backtest.json",
};

// Optional files: newer bakes. Missing/failed fetches resolve to null instead
// of breaking the whole app (older bakes won't have them).
const OPTIONAL_FILES = {
  papertrade: "papertrade.json",
  fundops: "fundops.json",
  meta: "meta.json",
};

// BASE_URL keeps fetches correct when the site is deployed under a sub-path.
const base = import.meta.env.BASE_URL || "/";

// ---------------------------------------------------------------------------
// Live backend (LingCode Cloud) — the same baked artifacts, served from a free
// managed Postgres via a read-only, RLS-enforced data API. The anon key below
// is a PUBLIC client key: it grants SELECT on the `datasets` table only. Set
// VITE_CHF_BACKEND_URL="" at build time to force the static /data/*.json path.
// ---------------------------------------------------------------------------
const BACKEND_URL =
  import.meta.env.VITE_CHF_BACKEND_URL ??
  "https://lingcode.dev/api/cloud/be/91f37821eecac54d365f7d0b";
const BACKEND_KEY =
  import.meta.env.VITE_CHF_BACKEND_KEY ??
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoidHJvbGVfOTFmMzc4MjFlZWNhYzU0ZDM2NWY3ZDBiIiwiaWF0IjoxNzg0MzMxMjM0fQ.Z3IOzcabUGomZrDexD2oTsJ41VW6x1RjE3afa9g9Kcg";

async function fetchJson(file) {
  const res = await fetch(`${base}data/${file}`);
  if (!res.ok) throw new Error(`Failed to load ${file} (${res.status})`);
  return res.json();
}

// Pull every dataset row from the backend in ONE request. Returns a map keyed
// by dataset name (summary, equity, …), or null if the backend is unreachable
// so the caller can fall back to static files.
async function fetchFromBackend() {
  if (!BACKEND_URL) return null;
  try {
    const res = await fetch(`${BACKEND_URL}/select`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${BACKEND_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ table: "datasets", limit: 200 }),
    });
    if (!res.ok) return null;
    const json = await res.json();
    const rows = json?.data;
    if (!Array.isArray(rows) || rows.length === 0) return null;
    return Object.fromEntries(rows.map((r) => [r.name, r.payload]));
  } catch {
    return null;
  }
}

// Module-level cache so navigating between routes never refetches.
let _cache = null;
let _promise = null;

async function loadAll() {
  if (_cache) return _cache;
  if (_promise) return _promise;

  _promise = (async () => {
    const backend = await fetchFromBackend();
    const fromBackend = (key) =>
      backend && backend[key] !== undefined ? backend[key] : undefined;

    const required = await Promise.all(
      Object.entries(FILES).map(async ([key, file]) => {
        const b = fromBackend(key);
        if (b !== undefined) return [key, b];
        return [key, await fetchJson(file)]; // static fallback
      })
    );
    const optional = await Promise.all(
      Object.entries(OPTIONAL_FILES).map(async ([key, file]) => {
        const b = fromBackend(key);
        if (b !== undefined) return [key, b];
        return [key, await fetchJson(file).catch(() => null)];
      })
    );

    _cache = Object.fromEntries([...required, ...optional]);
    return _cache;
  })();

  return _promise;
}

/**
 * Loads every artifact once (cached across the app). Returns
 * { data, error, loading }. `data` is keyed by the FILES keys above.
 *
 * The single seam between the UI and its data: it reads from the live
 * LingCode Cloud backend (managed Postgres, read-only data API) and falls
 * back to the baked /data/*.json files if the backend is unreachable.
 */
export function useChfData() {
  const [data, setData] = useState(_cache);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (_cache) return;
    let cancelled = false;
    loadAll()
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e));
    return () => {
      cancelled = true;
    };
  }, []);

  return { data, error, loading: !data && !error };
}
