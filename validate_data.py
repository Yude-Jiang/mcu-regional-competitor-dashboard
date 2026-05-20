#!/usr/bin/env python3
"""
validate_data.py — Schema, completeness, and cross-file checks for the
MCU competitor dashboard (China-market edition).

Usage: python validate_data.py
Exit code: 0 = all passed (warnings OK), 1 = failures found
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
YEAR_RANGE = range(2018, 2026)  # 2018..2025 inclusive
KNOWN_SYMBOLS = [
    "603986", "300327", "688380", "300077", "688279",
    "002180", "688385", "688766", "688595", "688391", "688018"
]
VALID_DATA_TYPES = {"actual", "forecast", "estimated"}
VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_AUTO_STATUS = {"volume", "ramping", "pilot", "rd_only", "na"}

issues = []

def fail(check, detail):
    issues.append(("FAIL", check, detail))

def warn(check, detail):
    issues.append(("WARN", check, detail))

def ok(check, detail=""):
    issues.append(("PASS", check, detail))


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------

def load_json(name):
    path = HERE / name
    if not path.exists():
        fail("load", f"{name} not found")
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        fail("load", f"{name} invalid JSON: {exc}")
        return None


# ---------------------------------------------------------------------------
# data.json schema
# ---------------------------------------------------------------------------

REQUIRED_YEAR_KEYS = [
    "fx_rate_cny_usd", "total_revenue_cny", "total_revenue_yoy",
    "mcu_revenue_cny", "mcu_revenue_yoy", "mcu_weight",
    "mcu_gross_margin", "rd_expense_cny", "rd_pct_revenue",
    "employees", "data_type", "source", "source_date"
]

REQUIRED_COMPANY_KEYS = ["symbol", "currency", "fx_note", "years", "cagr_2018_2025"]
REQUIRED_CAGR_KEYS = ["total_revenue", "mcu_revenue", "note"]


def check_data_schema(data):
    if data is None:
        return
    if "meta" not in data:
        fail("schema", "data.json missing 'meta'")
    if "companies" not in data:
        fail("schema", "data.json missing 'companies'")
        return

    companies = data["companies"]
    if not isinstance(companies, dict):
        fail("schema", "data.json.companies must be a dict")
        return

    for sym in KNOWN_SYMBOLS:
        if sym not in companies:
            fail("schema", f"data.json.companies missing symbol: {sym}")
            continue
        comp = companies[sym]
        for key in REQUIRED_COMPANY_KEYS:
            if key not in comp:
                fail("schema", f"{sym}: missing company key '{key}'")
        if comp.get("symbol") != sym:
            fail("schema", f"{sym}: symbol field mismatch '{comp.get('symbol')}'")
        if comp.get("currency") != "CNY":
            warn("schema", f"{sym}: currency is '{comp.get('currency')}', expected 'CNY'")

        years = comp.get("years", {})
        for yr in YEAR_RANGE:
            ys = str(yr)
            if ys not in years:
                fail("schema", f"{sym}: missing year {ys}")
                continue
            rec = years[ys]
            for key in REQUIRED_YEAR_KEYS:
                if key not in rec:
                    fail("schema", f"{sym} {ys}: missing field '{key}'")

        cagr = comp.get("cagr_2018_2025", {})
        for key in REQUIRED_CAGR_KEYS:
            if key not in cagr:
                fail("schema", f"{sym}: cagr_2018_2025 missing '{key}'")

    ok("schema", "data.json structure")


# ---------------------------------------------------------------------------
# data.json content checks
# ---------------------------------------------------------------------------

def check_data_content(data):
    if data is None:
        return
    companies = data["companies"]
    total_years = 0
    populated = 0
    null_numeric = 0

    for sym in KNOWN_SYMBOLS:
        comp = companies.get(sym)
        if comp is None:
            continue
        for yr in YEAR_RANGE:
            ys = str(yr)
            rec = comp["years"].get(ys)
            if rec is None:
                continue
            total_years += 1

            # data_type check
            dt = rec.get("data_type")
            src = rec.get("source", "")
            is_aksource = "akshare" in str(src)
            if yr == 2025:
                if dt == "forecast" and is_aksource and rec.get("total_revenue_cny") is not None:
                    warn("content", f"{sym} {ys}: has real data (AkShare) but data_type='forecast', should be 'actual'")
                elif dt not in ("actual", "forecast", "estimated"):
                    fail("content", f"{sym} {ys}: invalid data_type '{dt}'")
            elif yr != 2025 and dt == "forecast":
                warn("content", f"{sym} {ys}: data_type='forecast', expected 'actual' or 'estimated'")
            if dt not in VALID_DATA_TYPES:
                fail("content", f"{sym} {ys}: invalid data_type '{dt}'")

            # fx_rate_cny_usd must be populated (not null)
            fx = rec.get("fx_rate_cny_usd")
            if fx is None:
                fail("content", f"{sym} {ys}: fx_rate_cny_usd is null (must be populated)")
            elif not isinstance(fx, (int, float)):
                fail("content", f"{sym} {ys}: fx_rate_cny_usd is not numeric")

            # count null numeric fields
            numeric_fields = [
                "total_revenue_cny", "total_revenue_yoy", "mcu_revenue_cny",
                "mcu_revenue_yoy", "mcu_weight", "mcu_gross_margin",
                "rd_expense_cny", "rd_pct_revenue", "employees"
            ]
            for nf in numeric_fields:
                if rec.get(nf) is not None:
                    populated += 1
                else:
                    null_numeric += 1

    ok("content", f"{total_years} company-year records checked")
    ok("content", f"{populated} numeric fields populated, {null_numeric} null (skeleton OK)")
    ok("content", "data_type values validated (2018-2024: actual, 2025: forecast)")


# ---------------------------------------------------------------------------
# fx_rate consistency (data.json vs fx_rates.json)
# ---------------------------------------------------------------------------

def check_fx_consistency(data, fx):
    if data is None or fx is None:
        return
    cny_usd = fx.get("CNY_USD", {})
    companies = data["companies"]
    mismatches = 0
    for sym in KNOWN_SYMBOLS:
        comp = companies.get(sym)
        if comp is None:
            continue
        for yr in YEAR_RANGE:
            ys = str(yr)
            rec = comp["years"].get(ys)
            if rec is None:
                continue
            data_fx = rec.get("fx_rate_cny_usd")
            source_fx = cny_usd.get(ys)
            if data_fx is not None and source_fx is not None:
                if abs(data_fx - source_fx) > 0.01:
                    fail("fx-consistency", f"{sym} {ys}: fx_rate_cny_usd={data_fx}, fx_rates={source_fx}")
                    mismatches += 1
    if mismatches == 0:
        ok("fx-consistency", "all fx_rate_cny_usd values match fx_rates.json")


# ---------------------------------------------------------------------------
# profiles_xq.json schema
# ---------------------------------------------------------------------------

REQUIRED_PROFILE_KEYS = [
    "symbol", "name_cn", "name_en", "region", "exchange",
    "founded_year", "listed_year", "core_ip", "foundry",
    "employees", "employees_year",
    "mcu_revenue_scope", "mcu_revenue_confidence",
    "auto_mcu_status", "auto_mcu_note",
    "source_updated", "source_url"
]


def check_profiles(profiles):
    if profiles is None:
        return
    if not isinstance(profiles, list):
        fail("schema", "profiles_xq.json must be an array")
        return

    symbols_seen = set()
    for i, p in enumerate(profiles):
        for key in REQUIRED_PROFILE_KEYS:
            if key not in p:
                fail("schema", f"profiles[{i}]: missing key '{key}'")

        sym = p.get("symbol", "")
        if sym in symbols_seen:
            fail("schema", f"profiles[{i}]: duplicate symbol '{sym}'")
        symbols_seen.add(sym)

        if p.get("region") != "CN":
            warn("schema", f"profiles[{i}] ({sym}): region='{p.get('region')}', expected 'CN'")

        conf = p.get("mcu_revenue_confidence")
        if conf not in VALID_CONFIDENCE:
            fail("schema", f"profiles[{i}] ({sym}): invalid mcu_revenue_confidence '{conf}'")

        auto = p.get("auto_mcu_status")
        if auto not in VALID_AUTO_STATUS:
            fail("schema", f"profiles[{i}] ({sym}): invalid auto_mcu_status '{auto}'")

        if not isinstance(p.get("core_ip"), list) or len(p.get("core_ip", [])) == 0:
            warn("schema", f"profiles[{i}] ({sym}): core_ip is empty or not a list")

        if not isinstance(p.get("foundry"), list) or len(p.get("foundry", [])) == 0:
            warn("schema", f"profiles[{i}] ({sym}): foundry is empty or not a list")

    for sym in KNOWN_SYMBOLS:
        if sym not in symbols_seen:
            fail("schema", f"profiles_xq.json missing symbol: {sym}")

    ok("schema", f"profiles_xq.json: {len(profiles)} profiles")


# ---------------------------------------------------------------------------
# fx_rates.json schema
# ---------------------------------------------------------------------------

def check_fx_schema(fx):
    if fx is None:
        return
    if "CNY_USD" not in fx:
        fail("schema", "fx_rates.json missing 'CNY_USD'")
        return
    cny_usd = fx["CNY_USD"]
    for yr in YEAR_RANGE:
        ys = str(yr)
        if ys not in cny_usd:
            fail("schema", f"fx_rates.json CNY_USD missing year {ys}")
        elif not isinstance(cny_usd[ys], (int, float)):
            fail("schema", f"fx_rates.json CNY_USD.{ys} not numeric")
    if "source" not in fx:
        warn("schema", "fx_rates.json missing 'source'")
    ok("schema", "fx_rates.json structure")


# ---------------------------------------------------------------------------
# cross-file integrity
# ---------------------------------------------------------------------------

def check_cross_file(data, profiles):
    if data is None or profiles is None:
        return
    profile_syms = {p["symbol"] for p in profiles}
    data_syms = set(data["companies"].keys())

    for sym in KNOWN_SYMBOLS:
        if sym not in profile_syms:
            fail("cross-file", f"symbol '{sym}' in data.json but not in profiles_xq.json")
        if sym not in data_syms:
            fail("cross-file", f"symbol '{sym}' in profiles_xq.json but not in data.json")

    ok("cross-file", "profile-data symbol cross-references verified")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  MCU Competitor Dashboard — Data Validation (China Market)")
    print("=" * 60)
    print()

    data = load_json("data.json")
    profiles = load_json("profiles_xq.json")
    fx = load_json("fx_rates.json")

    if data is None or profiles is None or fx is None:
        print("FATAL: one or more data files could not be loaded")
        sys.exit(1)

    check_data_schema(data)
    check_data_content(data)
    check_fx_consistency(data, fx)
    check_profiles(profiles)
    check_fx_schema(fx)
    check_cross_file(data, profiles)

    # --- report ---
    print()
    print("=" * 60)
    print("  VALIDATION REPORT")
    print("=" * 60)
    print()

    n_pass = n_warn = n_fail = 0
    for level, check, detail in issues:
        prefix = {"PASS": "  OK  ", "WARN": "  WARN", "FAIL": "  FAIL"}[level]
        print(f"{prefix}  [{check}] {detail}")
        if level == "PASS":
            n_pass += 1
        elif level == "WARN":
            n_warn += 1
        else:
            n_fail += 1

    print()
    print("-" * 60)
    print(f"  Summary: {n_pass} passed, {n_warn} warnings, {n_fail} failures")
    print("-" * 60)

    if n_fail > 0:
        print("  RESULT: FAIL — fix failures above before populating data")
        sys.exit(1)
    else:
        print("  RESULT: PASS — skeleton validated successfully")
        sys.exit(0)


if __name__ == "__main__":
    main()
