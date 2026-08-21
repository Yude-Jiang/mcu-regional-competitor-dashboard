"""Shared helpers for AKShare fetch scripts."""

from __future__ import annotations


def notice_date_from_row(row) -> str | None:
    """Return YYYY-MM-DD from East Money NOTICE_DATE on a profit-sheet row."""
    raw = row.get("NOTICE_DATE")
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None
    return s[:10] if len(s) >= 10 else None
