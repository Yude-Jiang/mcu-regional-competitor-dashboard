#!/usr/bin/env python3
"""
fetch_data_akshare.py — Extract real financial data via AkShare for 11 Chinese MCU companies
and update data.json with verified, sourced data.

Data sources:
  - stock_financial_abstract() → total_revenue_cny, operating_cost, net_profit
  - stock_individual_info_em() → basic company info
  - Manual annual report data → MCU revenue, R&D expense (segment-level, not in standard APIs)

Only updates fields where AkShare returns valid data. Leaves null for fields requiring
segment-level disclosure not available through standard APIs.
"""

import json
import sys
import time
from pathlib import Path
from datetime import date

import akshare as ak
import pandas as pd

HERE = Path(__file__).parent

SYMBOLS = [
    "603986", "300327", "688380", "300077", "688279",
    "002180", "688385", "688766", "688595", "688391", "688018"
]

YEAR_RANGE = range(2018, 2026)
TODAY = date.today().isoformat()


def fetch_financial_abstract(symbol):
    """Fetch financial abstract from AkShare. Returns DataFrame or None."""
    try:
        fa = ak.stock_financial_abstract(symbol=symbol)
        time.sleep(0.5)  # rate limit
        return fa
    except Exception as e:
        print(f"  [{symbol}] stock_financial_abstract error: {e}")
        return None


def extract_annual_data(fa):
    """Extract annual data (Dec 31 columns) from financial_abstract DataFrame.

    Returns dict: {year_str: {indicator_name: value}}

    Matches rows by indicator name in the first column rather than hardcoded
    row indices, so the function is robust to AkShare format changes.
    """
    if fa is None or fa.empty:
        return {}

    # Map indicator name substrings → field names we care about
    INDICATOR_MAP = {
        "营业总收入": "total_revenue_cny",
        "营业成本": "total_cost_cny",
        "净利润": "net_profit_cny",
    }

    # Build a lookup: row_index → field_name by scanning the first column
    row_map = {}
    first_col = fa.columns[0]
    for idx, val in fa[first_col].items():
        name = str(val).strip()
        for keyword, field_name in INDICATOR_MAP.items():
            if keyword in name:
                row_map[idx] = field_name
                break

    cols = list(fa.columns)
    annual_cols = [c for c in cols if str(c).endswith("1231")]

    result = {}
    for col in annual_cols:
        year = str(col)[:4]
        if year not in [str(y) for y in YEAR_RANGE]:
            continue

        row_data = {}
        for row_idx, field_name in row_map.items():
            try:
                val = fa.iloc[row_idx][col]
                if pd.notna(val):
                    row_data[field_name] = float(val)
            except (IndexError, KeyError):
                pass

        if row_data:
            result[year] = row_data

    return result


def fetch_individual_info(symbol):
    """Fetch basic company info from East Money."""
    try:
        info = ak.stock_individual_info_em(symbol=symbol)
        time.sleep(0.3)
        result = {}
        for _, row in info.iterrows():
            key = str(row.iloc[0]).strip()
            val = row.iloc[1]
            if "总股本" in key:
                result["total_shares"] = val
            elif "流通股" in key:
                result["circulating_shares"] = val
            elif "总市值" in key:
                pass  # dynamic, not useful for historical
            elif "行业" in key:
                result["industry"] = val
            elif "上市时间" in key:
                result["listed_date"] = str(val)
        return result
    except Exception as e:
        print(f"  [{symbol}] stock_individual_info_em error: {e}")
        return {}


def compute_yoy(current, previous):
    """Compute year-over-year growth rate."""
    if current is None or previous is None or previous == 0:
        return None
    return round((current / previous - 1), 6)


def update_data_json(data_path, profiles_path):
    """Main update routine."""
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    companies = data["companies"]
    updated_symbols = []

    for sym in SYMBOLS:
        print(f"\nProcessing {sym}...")
        comp = companies.get(sym)
        if comp is None:
            print(f"  SKIP: {sym} not in data.json")
            continue

        # 1. Fetch financial abstract
        fa = fetch_financial_abstract(sym)
        annual = extract_annual_data(fa)

        if annual:
            updated_count = 0
            for year in [str(y) for y in YEAR_RANGE]:
                yr_data = annual.get(year, {})
                rec = comp["years"].get(year)
                if rec is None:
                    continue

                total_rev = yr_data.get("total_revenue_cny")
                if total_rev is not None and rec.get("total_revenue_cny") is None:
                    rec["total_revenue_cny"] = total_rev
                    rec["source"] = "akshare_stock_financial_abstract"
                    rec["source_date"] = TODAY
                    # 2025 annual report data is actual if published (by Apr 2026)
                    if year == "2025":
                        rec["data_type"] = "actual"
                    updated_count += 1

                    # Compute YoY if previous year available
                    prev_year = str(int(year) - 1)
                    prev_rec = comp["years"].get(prev_year, {})
                    prev_rev = prev_rec.get("total_revenue_cny")
                    if prev_rev and prev_rev > 0:
                        yoy = compute_yoy(total_rev, prev_rev)
                        if yoy is not None:
                            rec["total_revenue_yoy"] = yoy

                # NOTE: mcu_gross_margin is MCU-specific, NOT computed from total company cost.
                # Leave null — requires segment-level disclosure from annual reports.

            print(f"  Updated {updated_count} years of total_revenue_cny")
            if updated_count > 0:
                updated_symbols.append(sym)
        else:
            print(f"  No annual data extracted")

        # 2. Fetch individual info
        info = fetch_individual_info(sym)
        if info:
            print(f"  Individual info: listed_date={info.get('listed_date', 'N/A')}")

    # Update meta
    data["meta"]["last_updated"] = TODAY
    data["meta"]["fetch_method"] = "akshare stock_financial_abstract"

    # Write back
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"Updated {len(updated_symbols)} companies: {updated_symbols}")
    print(f"Data written to {data_path}")
    return updated_symbols


if __name__ == "__main__":
    data_path = HERE / "data.json"
    profiles_path = HERE / "profiles_xq.json"

    if not data_path.exists():
        print(f"ERROR: {data_path} not found")
        sys.exit(1)

    updated = update_data_json(str(data_path), str(profiles_path))

    if not updated:
        print("\nWARNING: No companies were updated. Check API connectivity.")
        sys.exit(1)

    print("\nDone. Run 'python validate_data.py' to verify.")
