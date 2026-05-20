#!/usr/bin/env python3
"""fetch_yjbb_quarterly.py — Fetch 业绩报表 (stock_yjbb_em) snapshots for 11 MCU companies.

Periods fetched: 2022Q4 (20221231), 2023Q4 (20231231), 2024Q4 (20241231)

Extracted fields per period:
  - 营业总收入     total revenue (亿 CNY, cumulative YTD = full-year for Q4)
  - 毛利率         gross margin %
  - 净利润YoY%     net profit year-on-year growth

Also merges gross_margin_pct + net_income_yoy_pct into data.json.

Usage:
    python fetch_yjbb_quarterly.py            # fetch + print + merge
    python fetch_yjbb_quarterly.py --no-merge  # fetch + print only
"""

import json
import logging
import sys
from pathlib import Path
from datetime import date

try:
    import akshare as ak
    import pandas as pd
except ImportError as e:
    sys.exit(f"Missing dependency: {e}\nInstall: pip install akshare pandas")

import bq_writer

log = logging.getLogger(__name__)

HERE = Path(__file__).parent

MCU_SYMBOLS = [
    "603986", "300327", "688380", "300077", "688279",
    "002180", "688385", "688766", "688595", "688391", "688018",
]

# Q4 date strings → full-year (cumulative Jan-Dec)
PERIODS: dict[str, int] = {
    "20221231": 2022,
    "20231231": 2023,
    "20241231": 2024,
}

# Candidate column names across AKShare versions
_COL_REVENUE = [
    "营业总收入-营业总收入",
    "营业收入",
    "营业总收入",
]
_COL_REVENUE_YOY = [
    "营业总收入-同比增长",
    "营业收入同比增长率",
]
_COL_NET_PROFIT_YOY = [
    "净利润-同比增长",
    "净利润同比增长率",
    "净利润增长率",
]
_COL_GROSS_MARGIN = [
    "毛利率",
    "销售毛利率",
]
_COL_NET_PROFIT = [
    "净利润-净利润",
    "净利润",
]
_COL_CODE = ["代码", "股票代码", "symbol"]


def _pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    # fuzzy: column that contains any candidate substring
    for c in candidates:
        matches = [col for col in df.columns if c in col]
        if matches:
            return matches[0]
    return None


def safe_float(v) -> float | None:
    try:
        import math
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def fetch_period(date_str: str) -> pd.DataFrame | None:
    """Call stock_yjbb_em and return filtered DataFrame for MCU symbols."""
    print(f"  Fetching {date_str}…", end=" ", flush=True)
    try:
        df = ak.stock_yjbb_em(date=date_str)
    except Exception as exc:
        print(f"FAILED: {exc}")
        return None

    if df is None or df.empty:
        print("empty response")
        return None

    print(f"got {len(df)} rows")

    # Normalise code column
    code_col = _pick_col(df, _COL_CODE)
    if not code_col:
        print(f"  WARNING: cannot find code column; columns={list(df.columns)[:10]}")
        return df  # return as-is, caller will try to filter

    df[code_col] = df[code_col].astype(str).str.strip().str.zfill(6)
    return df[df[code_col].isin(MCU_SYMBOLS)].copy()


def extract_row(df: pd.DataFrame, symbol: str, year: int) -> dict:
    """Extract one company's metrics from a period DataFrame."""
    code_col = _pick_col(df, _COL_CODE)
    if code_col is None:
        return {}

    rows = df[df[code_col].astype(str).str.zfill(6) == symbol]
    if rows.empty:
        return {}
    row = rows.iloc[0]

    def get(*candidates):
        col = _pick_col(pd.DataFrame(columns=df.columns), candidates)
        # Re-do pick against actual df
        for c in candidates:
            if c in df.columns:
                return safe_float(row.get(c))
        for c in candidates:
            matches = [col for col in df.columns if c in col]
            if matches:
                return safe_float(row.get(matches[0]))
        return None

    rev_cny = get(*_COL_REVENUE)        # 亿 CNY (cumulative Jan-Dec)
    rev_yoy = get(*_COL_REVENUE_YOY)    # %
    ni_yoy  = get(*_COL_NET_PROFIT_YOY) # %
    gm      = get(*_COL_GROSS_MARGIN)   # %
    ni      = get(*_COL_NET_PROFIT)     # 亿 CNY

    return {
        "year":              year,
        "symbol":            symbol,
        "total_revenue_cny_100m": rev_cny,   # 亿元
        "revenue_yoy_pct":   rev_yoy,
        "net_income_cny_100m": ni,
        "net_income_yoy_pct": ni_yoy,
        "gross_margin_pct":  gm,
        "source":            f"stock_yjbb_em {year}Q4",
    }


def print_table(records: list[dict], meta: dict) -> None:
    """Pretty-print a side-by-side comparison table."""
    years = sorted({r["year"] for r in records})
    by_sym: dict[str, dict[int, dict]] = {}
    for r in records:
        by_sym.setdefault(r["symbol"], {})[r["year"]] = r

    def fmt(v, suffix="", na="—"):
        return f"{v:.1f}{suffix}" if v is not None else na

    # Header
    yr_cols = "  ".join(f"{'  '.join([f'{y}Rev(亿)', f'{y}GM%', f'{y}NI-YoY%']):<42}" for y in years)
    print(f"\n{'─'*120}")
    print(f"{'公司':<14}{'代码':<10}  {yr_cols}")
    print(f"{'─'*120}")

    for sym in MCU_SYMBOLS:
        if sym not in by_sym:
            continue
        name = meta.get(sym, {}).get("name_cn", sym)
        row_parts = []
        for y in years:
            rd = by_sym[sym].get(y, {})
            rev = fmt(rd.get("total_revenue_cny_100m"))
            gm  = fmt(rd.get("gross_margin_pct"), "%")
            ni_yoy = fmt(rd.get("net_income_yoy_pct"), "%")
            row_parts.append(f"{rev:<12}  {gm:<10}  {ni_yoy:<14}")
        print(f"{name:<14}{sym:<10}  {'  '.join(row_parts)}")

    print(f"{'─'*120}")
    print("单位：营业收入=亿CNY；GM%=毛利率；NI-YoY%=净利润同比\n")


def merge_into_data_json(records: list[dict]) -> None:
    """Merge gross_margin_pct and net_income_yoy_pct into data.json."""
    path = HERE / "data.json"
    if not path.exists():
        print("data.json not found — skipping merge")
        return

    data = json.loads(path.read_text())
    merged = 0

    for r in records:
        sym = r["symbol"]
        yr  = str(r["year"])
        co  = data.get("companies", {}).get(sym)
        if co is None:
            continue
        fin = co.setdefault("financials", {}).setdefault(yr, {})

        for key in ("gross_margin_pct", "net_income_yoy_pct",
                    "revenue_yoy_pct", "net_income_cny_100m"):
            if r.get(key) is not None:
                fin[key] = r[key]

        # stock_yjbb_em returns revenue in yuan (not 亿), store directly
        if r.get("total_revenue_cny_100m") is not None and "total_revenue_yuan" not in fin:
            fin["total_revenue_yuan"] = r["total_revenue_cny_100m"]

        merged += 1

    data["generated_at"] = date.today().isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"Merged {merged} record(s) into data.json")


def sync_to_bigquery(records: list[dict]) -> None:
    """Upsert yjbb fields (gross_margin_pct, net_income_yoy_pct) into BigQuery."""
    if not bq_writer.is_available():
        return

    from google.cloud.bigquery import ScalarQueryParameter, QueryJobConfig

    bq = bq_writer._get_client()
    written = 0
    for r in records:
        sym = r["symbol"]
        yr  = r["year"]

        # Partial update: only overwrite yjbb fields, leave everything else alone
        sql = f"""
        MERGE `{bq_writer._PROJECT}.{bq_writer._DATASET}.financials` T
        USING (SELECT @symbol AS symbol, @year AS year, @period AS period) S
        ON T.symbol = S.symbol AND T.year = S.year AND T.period = S.period
        WHEN MATCHED THEN UPDATE SET
            gross_margin_pct   = COALESCE(@gross_margin_pct,   T.gross_margin_pct),
            net_income_yoy_pct = COALESCE(@net_income_yoy_pct, T.net_income_yoy_pct),
            revenue_yoy_pct    = COALESCE(@revenue_yoy_pct,    T.revenue_yoy_pct),
            updated_at         = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT
            (symbol, year, period, gross_margin_pct, net_income_yoy_pct,
             revenue_yoy_pct, updated_at)
        VALUES
            (@symbol, @year, @period, @gross_margin_pct, @net_income_yoy_pct,
             @revenue_yoy_pct, CURRENT_TIMESTAMP())
        """
        params = [
            ScalarQueryParameter("symbol",            "STRING",  sym),
            ScalarQueryParameter("year",              "INT64",   yr),
            ScalarQueryParameter("period",            "STRING",  "年报"),
            ScalarQueryParameter("gross_margin_pct",  "FLOAT64", r.get("gross_margin_pct")),
            ScalarQueryParameter("net_income_yoy_pct","FLOAT64", r.get("net_income_yoy_pct")),
            ScalarQueryParameter("revenue_yoy_pct",   "FLOAT64", r.get("revenue_yoy_pct")),
        ]
        try:
            bq.query(sql, job_config=QueryJobConfig(query_parameters=params)).result()
            written += 1
        except Exception as exc:
            log.warning("BQ yjbb write failed %s %s: %s", sym, yr, exc)

    print(f"BQ: synced {written}/{len(records)} yjbb record(s)")


def main() -> None:
    no_merge = "--no-merge" in sys.argv

    # Load company names for display
    meta_path = HERE / "companies_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    print("Fetching stock_yjbb_em for 11 MCU companies (2022Q4 / 2023Q4 / 2024Q4)…\n")

    all_records: list[dict] = []

    for date_str, year in PERIODS.items():
        df = fetch_period(date_str)
        if df is None:
            continue
        for sym in MCU_SYMBOLS:
            rec = extract_row(df, sym, year)
            if rec:
                all_records.append(rec)

    if not all_records:
        print("No data extracted — check AKShare connectivity.")
        sys.exit(1)

    print_table(all_records, meta)

    # Save to yjbb_quarterly.json
    out = {
        "generated_at": date.today().isoformat(),
        "periods": list(PERIODS.keys()),
        "records": all_records,
    }
    (HERE / "yjbb_quarterly.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2)
    )
    print(f"Wrote yjbb_quarterly.json  ({len(all_records)} records)")

    if not no_merge:
        merge_into_data_json(all_records)

    sync_to_bigquery(all_records)


if __name__ == "__main__":
    main()
