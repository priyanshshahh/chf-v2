"""CoinMarketCap data pipeline (completion + monthly maintenance).

See specs/001-cmc-data-pipeline/. Shared HTTP client, manifest, and atomic
upsert live here; one collector per data domain lives in ``collectors/``.
"""
