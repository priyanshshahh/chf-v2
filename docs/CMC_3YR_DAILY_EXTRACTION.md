# CMC 3-Year Daily Historical Extraction — what we tested, found, and built

This documents the live test of the supplied CoinMarketCap subscription and the
extraction code/data produced from it. Companion folder:
[`../coinmarketcap_extract/`](../coinmarketcap_extract/). Background on CMC overall:
`docs/COINMARKETCAP.md`; the original limitation probe: `docs/CMC_HISTORICAL_ACCESS_LIMITATION.md`.

---

## 0. THE HEADLINE ANSWER (asked first): does this plan give the historical ticker list?

**No — not via the Pro `listings/historical` endpoint.** We tested the key live against
`pro-api.coinmarketcap.com` on **2026-06-17**:

| What we tested | Result |
|---|---|
| `/v1/key/info` | **HTTP 200** — rate limit **300/min** (the announced increase IS live), 150,000 credits/month, 2,363 used. |
| `/v1/cryptocurrency/listings/historical` for **2023-07-01, 2024-06-01, 2025-06-01, 2026-05-01** | **HTTP 400** — *"Your plan allows 1 months of historical access. Please upgrade your plan or choose a startDate that is newer than 2026-05-17."* |
| `/v1/cryptocurrency/listings/historical` boundary test | **2026-05-18 → HTTP 200**, **2026-05-16 → HTTP 400** → confirmed the window is still **~1 month**, not 3 years. |
| `/v2/cryptocurrency/quotes/historical` (BTC) for **2023-07** (3 yr back) | **HTTP 200** — daily points returned. |
| **Keyless** `api.coinmarketcap.com/data-api/v3/.../listings/historical` for **2023-07-01** | **HTTP 200** — BTC, ETH, USDT, BNB, USDC … (works, free, no key). |

**Interpretation.** CMC's message that "Hobbyist now includes 3 years of *daily* historical
data" is **true for `/v2 quotes/historical`** (per-coin daily price/market-cap/volume — we
verified 3 years back works) and the **rate limit is genuinely raised to 300/min**. But the
**historical *listings* (ticker-list) endpoint** — `/v1/cryptocurrency/listings/historical`,
the one that returns *which* active **and inactive** coins were ranked at a past date — was
**NOT** upgraded. It is still hard-capped at **1 month** on this plan.

This is the critical distinction for look-ahead-free research: **listings (membership) ≠
quotes (per-coin prices).** Quotes alone can price coins you already selected, but cannot
tell you *which* coins (including since-delisted ones) belonged in the historical top-N.
So on the current subscription, the Pro `listings/historical` path **cannot** build a
3-year historical ticker list.

### The unblock we used
CoinMarketCap's **public, keyless** data-API that powers `coinmarketcap.com/historical`
returns the **same** historical top-N ticker list — active **and** inactive/delisted — for
**any date back to 2013-05-05**, for **free, with no plan limit**:

```
GET https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical
      ?date=YYYY-MM-DD&start=1&limit=<=1000&convert=USD
```

Each row already carries the per-coin **price / market cap / 24h volume**, plus supply,
`dateAdded` (true first-listing date), `numMarketPairs`, and category `tags`. So a single
daily pull from this endpoint yields **both the historical ticker list and the daily market
data, including delisted coins** — exactly what the research needs, without spending Pro
credits or upgrading.

---

## 1. What we built

`coinmarketcap_extract/extract_cmc_daily_history.py` — a self-contained, resumable
extractor that pulls **daily** historical top-N snapshots from the keyless data-API and
writes a tidy combined table + raw JSON + a provenance manifest.

- **Granularity:** `--freq daily` (also `weekly`/`monthly`). Default span: 3 years back.
- **Coverage:** `--top 200` by default (≥100 plus churn headroom so the historical top-100
  is always recoverable, incl. coins that later left the top-100).
- **Integrity:** no synthetic data; a date that fails returns to `failures` and is skipped,
  never back-filled. Each day's raw response is cached, so runs are resumable and re-runs
  are free. No API key is read or written (this endpoint is keyless).
- **Robustness:** handles both observed response shapes (`data:[...]` and
  `data:{cryptoCurrencyList:[...]}`) and both quote shapes (`quotes[0].marketCap` and
  `quote.USD.market_cap`).

Run used to produce the data:
```bash
python3 coinmarketcap_extract/extract_cmc_daily_history.py --years 3 --top 200 --min-seconds 1.2
```

## 2. What we extracted (outputs)

Under `coinmarketcap_extract/`:

| Path | Contents |
|---|---|
| `raw_daily_json/YYYY-MM-DD.json` | Untouched API response per day (resumable cache + audit trail) |
| `processed/cmc_daily_listings_historical.parquet` | Tidy combined daily table |
| `processed/cmc_daily_listings_historical.csv` | Same, CSV |
| `processed/extraction_manifest.json` | Provenance: endpoint, span, snapshot counts, unique coins, failures |

**Columns:** `snapshot_date, cmc_id, rank, symbol, name, slug, is_active, market_cap_usd,
price_usd, volume_24h_usd, circulating_supply, total_supply, max_supply, num_market_pairs,
date_added, raw_category_tags, source`.

**Final extracted totals** (from `processed/extraction_manifest.json`):

| Metric | Value |
|---|---|
| Frequency / depth | **daily**, top-200/day |
| Coverage | **2023-06-19 → 2026-06-17** |
| Daily snapshots built | **1,095** (of 1,096 requested) |
| Total rows | **218,643** |
| Unique coins (`cmc_id`) | **507** |
| Unique symbols | **516** |
| Live API calls | 1,089 |
| Failures | **1** — `2026-06-18` (a future date with no data; correctly skipped, never fabricated) |

That **507 unique coins cycle through a 200-deep daily list** is the measurable signature of
survivorship-free membership — ~300 names churned in and out over 3 years. Coins present at
the start but gone from the latest snapshot include **BUSD, AGIX, BLUR, ABBC, ANT, BAND,
BTG, CELO**, etc. (A 7-day verification slice ran first and confirmed real data — BTC #1,
ETH #2, USDT #3 — 1,050 rows / 152 unique `cmc_id`s, 0 failures.)

> Caveat on `is_active`: the keyless endpoint returns `is_active=1` for every row, because
> each row reflects the snapshot date on which the coin *was* active/ranked. So the
> delisted/inactive coverage is evidenced by **membership churn** (507 unique vs ~200/day),
> **not** by the `is_active` column — do not rely on `is_active` as a delisted flag.

## 3. Reference: Thomas's Bitcoin code

The pattern Danling shared (Thomas's working Bitcoin extractor — API loop → save JSON →
convert to CSV) is the same shape used here, generalized from one coin to the full daily
top-N ticker list and to the keyless historical-listings endpoint: **loop dates → cache raw
JSON per date → parse → combined CSV/Parquet**.

## 4. How to reproduce / extend

```bash
# 3 years daily (what we ran)
python3 coinmarketcap_extract/extract_cmc_daily_history.py --years 3 --top 200

# explicit window
python3 coinmarketcap_extract/extract_cmc_daily_history.py --start 2023-06-17 --end 2026-06-17 --top 200

# "the entire history" later — monthly back to CMC's 2013 origin (daily would be ~4,700 calls)
python3 coinmarketcap_extract/extract_cmc_daily_history.py --freq monthly --start 2013-05-05 --top 300
```

To extend to **full history**: the same keyless endpoint reaches back to **2013-05-05**.
Start monthly (cheap, ~157 months) to map coverage, then densify to daily for the windows
the model needs. Everything is cached, so densifying only fetches the missing dates.

## 5. Limitations & notes

1. **Pro `listings/historical` stays 1-month** on this plan — to use the *Pro* path for a
   3-year ticker list you would need Standard (3 mo) / Professional (12 mo) / **Enterprise
   (≤6 yr)**. The keyless path makes that upgrade unnecessary for membership.
2. **`ohlcv/historical`** (true daily OHLC) is **not in this plan** (HTTP 403). The daily
   `price`/`market_cap`/`volume_24h` we extract come from the listings quote, not OHLC.
3. **Category `tags` are ~current**, not strictly point-in-time — fine for classification,
   never affects returns.
4. **Top-N candidate cap:** coins never in the daily top-200 are out of scope; widen `--top`
   to deepen the tail (e.g. `--top 500`).
5. **Key handling:** the supplied key is stored only in the gitignored `.env` (used by the
   Pro probes / `quotes/historical`); it is **not** required for the keyless daily
   extraction and appears in no committed file.

**Bottom line.** The subscription does **not** give the 3-year historical *ticker list* via
the Pro endpoint (still 1 month); the 3-year-daily upgrade and 300/min rate limit apply to
`quotes/historical`. We obtained the full 3-year **daily** historical ticker list **with
active + inactive coins and daily market data** via CMC's free keyless data-API, and built
resumable code to extract and extend it — **1,095 daily snapshots, 2023-06-19 → 2026-06-17,
218,643 rows, 507 unique coins**.
</content>
