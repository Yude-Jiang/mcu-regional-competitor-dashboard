#!/usr/bin/env python3
"""validate_data.py — Basic schema checks on data.json."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
REQUIRED_META = {"name_cn", "name_en", "symbol", "mcu_strategy", "mcu_confidence"}
EXPECTED_SYMBOLS = {
    "603986","300327","688380","300077","688279",
    "002180","688385","688694","688595","688391","688018"
}


def main() -> None:
    path = HERE / "data.json"
    if not path.exists():
        print("FAIL: data.json not found — run fetch_mcu_data.py first")
        sys.exit(1)

    data = json.loads(path.read_text())
    errors: list[str] = []

    companies = data.get("companies", {})
    missing = EXPECTED_SYMBOLS - set(companies)
    if missing:
        errors.append(f"Missing companies: {missing}")

    for sym, co in companies.items():
        meta = co.get("meta", {})
        for k in REQUIRED_META:
            if not meta.get(k):
                errors.append(f"[{sym}] meta missing: {k}")

        fin = co.get("financials", {})
        for yr, row in fin.items():
            if row.get("total_revenue_musd") is not None:
                rev = row["total_revenue_musd"]
                if rev < 0 or rev > 100_000:
                    errors.append(f"[{sym}][{yr}] suspicious total_revenue_musd={rev}")

    if errors:
        print(f"VALIDATION FAILED ({len(errors)} issue(s)):")
        for e in errors:
            print(f"  • {e}")
        sys.exit(1)
    else:
        n_fin = sum(
            len(co.get("financials", {}))
            for co in companies.values()
        )
        print(f"OK — {len(companies)} companies, {n_fin} year-rows validated.")


if __name__ == "__main__":
    main()
