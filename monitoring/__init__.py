"""Project CHF operational monitoring package.

Every module is a standalone CLI (``python -m monitoring.<name>``) that reads
pipeline artifacts read-only, writes JSON + parquet reports under
``data/reports/monitoring/`` and exits 0 (ok/warn) or 1 (alert).
"""
