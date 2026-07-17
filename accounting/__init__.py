"""CHF fund-accounting layer.

Independent, administrator-style shadow accounting for the paper-trading
books: an append-only event ledger, ledger-derived NAV reconstruction,
management / performance fee accounting with high-water marks, dual-book
reconciliation against the papertrade engine, and a tamper-evident audit log.

This package never mutates anything under ``data/papertrade/`` — it only
reads engine outputs and writes its own artifacts under ``data/accounting/``.
"""

from __future__ import annotations
