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

async function fetchJson(file) {
  const res = await fetch(`${base}data/${file}`);
  if (!res.ok) throw new Error(`Failed to load ${file} (${res.status})`);
  return res.json();
}

// Module-level cache so navigating between routes never refetches.
let _cache = null;
let _promise = null;

function loadAll() {
  if (_cache) return Promise.resolve(_cache);
  if (_promise) return _promise;
  _promise = Promise.all([
    ...Object.entries(FILES).map(async ([key, file]) => [key, await fetchJson(file)]),
    ...Object.entries(OPTIONAL_FILES).map(async ([key, file]) => [
      key,
      await fetchJson(file).catch(() => null),
    ]),
  ]).then((entries) => {
    _cache = Object.fromEntries(entries);
    return _cache;
  });
  return _promise;
}

/**
 * Loads every baked artifact once (cached across the app). Returns
 * { data, error, loading }. `data` is keyed by the FILES keys above.
 *
 * This is the single seam between the static site and the pipeline: point the
 * fetch at the live FastAPI service instead of /data/*.json and the UI is
 * unchanged.
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
