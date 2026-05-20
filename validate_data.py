#!/usr/bin/env python3
"""validate_data.py — Schema and content checks for data.json / profiles_xq.json.

Exit 0 = PASS, Exit 1 = FAIL.
Run after every edit: python validate_data.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent

KNOWN_SYMBOLS = [
    "603986", "300327", "688380", "300077", "688279",
    "002180", "688385", "688766", "688595", "688391", "688018"
]

VALID_DATA_TYPES   = {"actual", "forecast", "estimated", "derived", "reported",
                      "unavailable", "pending"}
VALID_CONFIDENCE   = {"high", "medium", "low", "na"}
VALID_AUTO_STATUS  = {"volume", "ramping", "pilot", "rd_only", "na"}
VALID_MCU_STRATEGY = {"segment_reported", "segment_industrial", "segment_estimated",
                      "total_proxy", "estimated", "subsidiary_geehy", "na"}

REQUIRED_YEAR_KEYS = [
    "total_revenue_yuan",
    "total_revenue_musd",
    "mcu_revenue_yuan",
    "mcu_revenue_musd",
    "mcu_data_type",
    "mcu_confidence",
    "filing_status",
]

REQUIRED_PROFILE_KEYS = [
    "symbol", "name_cn", "name_en", "mcu_revenue_scope",
    "mcu_revenue_confidence", "auto_mcu_status",
]

REQUIRED_META_KEYS = [
    "name_cn", "name_en", "symbol",
    "mcu_strategy", "mcu_confidence",
]

failures = []
warnings = []


def fail(section, msg):
    failures.append(f"[{section}] {msg}")


def warn(section, msg):
    warnings.append(f"[{section}] {msg}")


# ── data.json ─────────────────────────────────────────────────────────────────

data_path = HERE / "data.json"
if not data_path.exists():
    print("FAIL: data.json not found — run fetch_mcu_data.py first")
    sys.exit(1)

data = json.loads(data_path.read_text())
companies = data.get("companies", {})

# Symbol completeness
missing = set(KNOWN_SYMBOLS) - set(companies)
if missing:
    fail("schema", f"data.json missing companies: {missing}")

extra = set(companies) - set(KNOWN_SYMBOLS)
if extra:
    warn("schema", f"data.json has unexpected symbols: {extra}")

for sym in KNOWN_SYMBOLS:
    co = companies.get(sym)
    if not co:
        continue

    # Meta fields
    meta = co.get("meta", {})
    for k in REQUIRED_META_KEYS:
        if not meta.get(k):
            fail("meta", f"[{sym}] missing meta.{k}")

    # mcu_strategy valid value
    strat = meta.get("mcu_strategy", "")
    if strat and strat not in VALID_MCU_STRATEGY:
        fail("meta", f"[{sym}] invalid mcu_strategy={strat!r}")

    # ⚠️  Ninestar guard: total_revenue_yuan must NOT be the group total (264B)
    if sym == "002180":
        for yr, row in co.get("financials", {}).items():
            rev = row.get("total_revenue_yuan")
            if rev and rev > 20_000_000_000:  # > 200亿 = 集团口径
                fail("content",
                     f"[{sym}][{yr}] total_revenue_yuan={rev:.0f} looks like "
                     f"Ninestar GROUP total (264B). Must be Geehy subsidiary only.")

    # Year rows
    financials = co.get("financials", {})
    if not financials:
        warn("content", f"[{sym}] financials is empty")
        continue

    for yr, row in financials.items():
        # Required keys present
        for k in REQUIRED_YEAR_KEYS:
            if k not in row:
                fail("schema", f"[{sym}][{yr}] missing field: {k}")

        # data_type valid
        dt = row.get("mcu_data_type", "")
        if dt and dt not in VALID_DATA_TYPES:
            fail("content", f"[{sym}][{yr}] invalid mcu_data_type={dt!r}")

        # confidence valid
        conf = row.get("mcu_confidence", "")
        if conf and conf not in VALID_CONFIDENCE:
            fail("content", f"[{sym}][{yr}] invalid mcu_confidence={conf!r}")

        # Revenue sanity
        rev = row.get("total_revenue_yuan")
        if rev is not None and (rev < 0 or rev > 500_000_000_000):
            fail("content", f"[{sym}][{yr}] suspicious total_revenue_yuan={rev}")

        mcu = row.get("mcu_revenue_yuan")
        if mcu is not None and rev is not None and rev > 0:
            ratio = mcu / rev
            if ratio > 1.05:
                fail("content",
                     f"[{sym}][{yr}] mcu_revenue_yuan > total_revenue_yuan "
                     f"(ratio={ratio:.2f})")

        # MUSD consistency check (allow 5% tolerance for FX rounding)
        rev_musd  = row.get("total_revenue_musd")
        mcu_musd  = row.get("mcu_revenue_musd")
        fx_rate   = row.get("fx_rate_cny_usd")

        if rev is not None and rev_musd is not None and fx_rate:
            expected = rev / fx_rate / 1_000_000
            if abs(expected - rev_musd) / max(expected, 0.01) > 0.05:
                warn("fx",
                     f"[{sym}][{yr}] total_revenue_musd={rev_musd:.1f} "
                     f"vs CNY/FX={expected:.1f} — gap >5%")

        if mcu is not None and mcu_musd is not None and fx_rate:
            expected = mcu / fx_rate / 1_000_000
            if abs(expected - mcu_musd) / max(expected, 0.01) > 0.05:
                warn("fx",
                     f"[{sym}][{yr}] mcu_revenue_musd={mcu_musd:.1f} "
                     f"vs CNY/FX={expected:.1f} — gap >5%")

        # Estimated data must have source note
        if dt in ("estimated", "derived") and not row.get("mcu_source"):
            warn("content",
                 f"[{sym}][{yr}] mcu_data_type={dt!r} but mcu_source is empty")


# ── profiles_xq.json ──────────────────────────────────────────────────────────

profiles_path = HERE / "profiles_xq.json"
if not profiles_path.exists():
    fail("schema", "profiles_xq.json not found")
else:
    profiles = json.loads(profiles_path.read_text())
    profile_symbols = {p.get("symbol") for p in profiles}

    for sym in KNOWN_SYMBOLS:
        if sym not in profile_symbols:
            fail("cross-file", f"symbol {sym} missing from profiles_xq.json")

    for p in profiles:
        sym = p.get("symbol", "?")
        for k in REQUIRED_PROFILE_KEYS:
            if not p.get(k):
                fail("profiles", f"[{sym}] profiles_xq missing: {k}")

        conf = p.get("mcu_revenue_confidence", "")
        if conf and conf not in VALID_CONFIDENCE:
            fail("profiles", f"[{sym}] invalid mcu_revenue_confidence={conf!r}")

        auto = p.get("auto_mcu_status", "")
        if auto and auto not in VALID_AUTO_STATUS:
            fail("profiles", f"[{sym}] invalid auto_mcu_status={auto!r}")

    # Cross-file: profiles symbol set must match KNOWN_SYMBOLS
    extra_p = profile_symbols - set(KNOWN_SYMBOLS)
    if extra_p:
        warn("cross-file", f"profiles_xq has extra symbols: {extra_p}")


# ── companies_meta.json ───────────────────────────────────────────────────────

meta_path = HERE / "companies_meta.json"
if meta_path.exists():
    meta_all = json.loads(meta_path.read_text())
    for sym in KNOWN_SYMBOLS:
        if sym not in meta_all:
            warn("cross-file", f"companies_meta.json missing symbol: {sym}")
        else:
            strat = meta_all[sym].get("mcu_strategy", "")
            if strat and strat not in VALID_MCU_STRATEGY:
                fail("meta", f"[{sym}] companies_meta invalid mcu_strategy={strat!r}")


# ── fx_rates.json ─────────────────────────────────────────────────────────────

fx_path = HERE / "fx_rates.json"
if not fx_path.exists():
    fail("schema", "fx_rates.json not found — required for MUSD conversion")
else:
    fx = json.loads(fx_path.read_text())
    cny_usd = fx.get("CNY_USD", {})
    for yr in range(2018, 2027):
        if str(yr) not in cny_usd:
            warn("fx", f"fx_rates.json missing year {yr}")


# ── Report ────────────────────────────────────────────────────────────────────

print(f"\n{'='*55}")
if warnings:
    print(f"WARN ({len(warnings)} warning(s)):")
    for w in warnings:
        print(f"  ⚠  {w}")
    print()

if failures:
    print(f"FAIL ({len(failures)} issue(s)):")
    for f in failures:
        print(f"  ✗  {f}")
    print(f"{'='*55}")
    sys.exit(1)

n_cos   = len([s for s in KNOWN_SYMBOLS if s in companies])
n_rows  = sum(len(co.get("financials", {})) for co in companies.values())
n_mcu   = sum(
    1 for co in companies.values()
    for row in co.get("financials", {}).values()
    if row.get("mcu_revenue_yuan") is not None
)
print(f"PASS — {n_cos} companies | {n_rows} year-rows | {n_mcu} MCU data points")
print(f"{'='*55}\n")
sys.exit(0)
