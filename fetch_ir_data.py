#!/usr/bin/env python3
"""
fetch_ir_data.py — Fetch IR meeting records from 巨潮资讯网 via AkShare
for companies whose MCU revenue relies on management guidance coefficients.

Target companies (C-class methodology):
  - 300077 国民技术 — MCU coefficient from IR verbal guidance
  - 688595 芯海科技 — MCU coefficient from IR verbal guidance
  - 688279 峰岹科技 — product structure discussion in IR records
  - 002180 纳思达   — subsidiary segment data (from annual report, not IR)

Usage:
  python fetch_ir_data.py                    # fetch all C-class companies
  python fetch_ir_data.py --symbol 300077    # single company
  python fetch_ir_data.py --since 2025-01-01 # date filter
"""

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent

# Companies that need IR-based coefficient verification
IR_SYMBOLS = {
    "300077": {"name": "国民技术", "field": "mcu_revenue_cny", "method": "coefficient_27pct"},
    "688595": {"name": "芯海科技", "field": "mcu_revenue_cny", "method": "coefficient_45pct"},
    "688279": {"name": "峰岹科技", "field": "mcu_revenue_cny", "method": "coefficient_65_70pct"},
}

OUTPUT_PATH = HERE / "ir_records.json"


def fetch_irm_cninfo(symbol, since_date=None):
    """Fetch investor Q&A records from Cninfo via AkShare stock_irm_cninfo.

    Column layout (position-based, resilient to encoding):
      [0] stock code  [1] company name  [4] question text
      [7] ask time    [8] reply time    [12] reply content  [13] reply cont'd
    """
    try:
        import akshare as ak

        df = ak.stock_irm_cninfo(symbol=symbol)
        time.sleep(0.5)

        if df is None or df.empty:
            print(f"  [{symbol}] No Q&A records found")
            return []

        # Date column at [7] = ask time, filter by since_date
        records = []
        for _, row in df.iterrows():
            try:
                ask_time = str(row.iloc[7])[:10]  # YYYY-MM-DD
            except (IndexError, TypeError):
                ask_time = ""

            if since_date and ask_time < since_date:
                continue

            question = str(row.iloc[4]) if len(row) > 4 else ""
            reply = ""
            if len(row) > 12 and row.iloc[12] is not None:
                reply = str(row.iloc[12])
            if len(row) > 13 and row.iloc[13] is not None and str(row.iloc[13]) != "None":
                reply += str(row.iloc[13])

            records.append({
                "symbol": symbol,
                "date": ask_time,
                "question": question,
                "reply": reply,
            })

        print(f"  [{symbol}] Found {len(records)} Q&A records (since {since_date or 'all'})")
        return records

    except ImportError:
        print("  [!] akshare not installed. Run: pip install akshare")
        return []
    except Exception as e:
        print(f"  [{symbol}] Error: {e}")
        return []


def extract_mcu_info(records):
    """Extract MCU-related Q&A using keyword search on question+reply text."""
    keywords = ["MCU", "微控制器", "占比", "营收比例", "收入结构",
                "产品结构", "芯片收入", "毛利率", "车规",
                "通用MCU", "安全芯片", "电机", "计量", "模组"]

    relevant = []
    for rec in records:
        text = rec.get("question", "") + " " + rec.get("reply", "")
        hits = [kw for kw in keywords if kw in text]
        if hits:
            rec["mcu_keywords"] = hits
            relevant.append(rec)

    return relevant


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fetch investor Q&A from Cninfo via AkShare")
    parser.add_argument("--symbol", type=str, help="Single stock symbol")
    parser.add_argument("--since", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--output", type=str, default=str(OUTPUT_PATH), help="Output JSON path")
    args = parser.parse_args()

    symbols_to_fetch = [args.symbol] if args.symbol else list(IR_SYMBOLS.keys())

    print(f"{'=' * 60}")
    print(f"  MCU IR Q&A Fetcher — AkShare stock_irm_cninfo")
    print(f"  Target companies: {len(symbols_to_fetch)}")
    print(f"  Since: {args.since or 'all time'}")
    print(f"{'=' * 60}\n")

    all_records = {}
    for sym in symbols_to_fetch:
        info = IR_SYMBOLS.get(sym, {})
        print(f"Fetching {sym} {info.get('name', '')} ...")
        records = fetch_irm_cninfo(sym, since_date=args.since)
        mcu_relevant = extract_mcu_info(records)
        all_records[sym] = {
            "name": info.get("name", ""),
            "method": info.get("method", ""),
            "total_records": len(records),
            "mcu_relevant": len(mcu_relevant),
            "records": mcu_relevant,
            "fetched_at": date.today().isoformat(),
        }
        print(f"  -> {len(mcu_relevant)} MCU-relevant out of {len(records)} total\n")

    # Save
    output_path = Path(args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    print(f"Saved to {output_path}")

    # Summary
    print(f"\n{'─' * 60}")
    print(f"  Summary:")
    for sym, data in all_records.items():
        print(f"  {sym} {data['name']}: {data['mcu_relevant']}/{data['total_records']} MCU Q&A records")
    print(f"{'─' * 60}")


if __name__ == "__main__":
    main()
