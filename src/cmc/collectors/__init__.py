"""One collector module per CMC data domain.

Each exposes ``run(client, base_dir, start=None, end=None) -> dict`` which writes
``<dataset>.parquet`` + ``manifest.json`` + a retained ``raw_api_samples/`` JSON
under its dataset directory, and returns the manifest dict. All monetary fields
are USD; all dates are ``YYYY-MM-DD``.
"""
