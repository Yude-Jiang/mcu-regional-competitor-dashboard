#!/usr/bin/env python3
"""fetch_mcu_data.py — Fetch annual financials for 11 MCU companies via AKShare,
apply per-company MCU revenue derivation rules, and write data.json.

Usage:
    python fetch_mcu_data.py            # fetch all companies
    python fetch_mcu_data.py 603986     # single company (debug)
"""

import json
import logging
import math
import sys
from datetime import date
from pathlib import Path

try:
    import akshare as ak
    import pandas as pd
except ImportError as e:
    sys.exit(f"Missing dependency: {e}\nInstall: pip install akshare pandas")

import bq_writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HERE = Path(__file__).parent
YEARS = list(range(2018, 2026))  # 2018–2025 (8 years)

# Annual average CNY per 1 USD — loaded from fx_rates.json (authoritative source)
_fx_path = HERE / "fx_rates.json"
if _fx_path.exists():
    FX: dict[int, float] = {
        int(k): v
        for k, v in json.loads(_fx_path.read_text())["CNY_USD"].items()
    }
else:
    FX = {
        2018: 6.6174, 2019: 6.8985, 2020: 6.8976, 2021: 6.4515,
        2022: 6.7261, 2023: 7.0809, 2024: 7.1900, 2025: 7.2200,
    }


# ── helpers ───────────────────────────────────────────────────────────────────

def to_musd(yuan: float | None, year: int) -> float | None:
    if yuan is None:
        return None
    return round(yuan / FX.get(year, 7.2) / 1_000_000, 2)


def safe_float(v) -> float | None:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def extract_year(col) -> int | None:
    try:
        return int(str(col)[:4])
    except (ValueError, TypeError):
        return None


def em_symbol(code: str) -> str:
    """Add exchange prefix required by East Money AKShare functions.

    SH = Shanghai (6xxxxx, 688xxx STAR)
    SZ = Shenzhen (3xxxxx ChiNext, 0xxxxx main board)
    """
    return ("SH" if code.startswith("6") else "SZ") + code


# ── AKShare fetchers ──────────────────────────────────────────────────────────

def _parse_long_df(df: "pd.DataFrame", annual_only: bool = True) -> dict[int, dict]:
    """Parse long-format profit sheet: rows=periods, cols=English field names.

    AKShare 1.18+ returns:
      REPORT_DATE, TOTAL_OPERATE_INCOME, PARENT_NETPROFIT, RESEARCH_EXPENSE, ...
    """
    out: dict[int, dict] = {}
    for _, row in df.iterrows():
        date_str = str(row.get("REPORT_DATE", ""))
        year = extract_year(date_str)
        if year not in YEARS:
            continue
        # For quarterly endpoint filter to Q4 (annual equivalent)
        if annual_only and not (date_str.endswith("12-31") or date_str.endswith("1231")):
            continue
        if year in out:
            continue  # keep first (most recent) occurrence

        revenue = safe_float(row.get("TOTAL_OPERATE_INCOME") or row.get("OPERATE_INCOME"))
        net_inc = safe_float(row.get("PARENT_NETPROFIT") or row.get("NETPROFIT"))
        rd      = safe_float(row.get("RESEARCH_EXPENSE") or row.get("ME_RESEARCH_EXPENSE"))

        if revenue is not None:
            out[year] = {
                "total_revenue_yuan": revenue,
                "net_income_yuan":    net_inc,
                "rd_expense_yuan":    rd,
            }
    return out


def fetch_profit_sheet(symbol: str) -> dict[int, dict]:
    """Annual P&L from East Money (AKShare 1.18+).

    Both endpoints return long-format DataFrames (rows=periods, cols=fields).
    yearly_em   → 15 annual rows    (preferred, one API call)
    report_em   → 48 quarterly rows (fallback, filter Q4)
    """
    sym = em_symbol(symbol)

    # Primary: yearly endpoint — already filtered to annual reports
    try:
        df = ak.stock_profit_sheet_by_yearly_em(symbol=sym)
        if df is not None and not df.empty and "REPORT_DATE" in df.columns:
            result = _parse_long_df(df, annual_only=False)
            if result:
                return result
    except Exception as exc:
        log.debug("  [%s] yearly profit_sheet error: %s", symbol, exc)

    # Fallback: report endpoint — all quarters, filter Dec-31
    log.info("  [%s] yearly endpoint empty — trying report fallback", symbol)
    try:
        df = ak.stock_profit_sheet_by_report_em(symbol=sym)
        if df is not None and not df.empty and "REPORT_DATE" in df.columns:
            result = _parse_long_df(df, annual_only=True)
            if result:
                return result
    except Exception as exc:
        log.debug("  [%s] report_em error: %s", symbol, exc)

    log.warning("  [%s] profit_sheet: no data from any source", symbol)
    return {}


def fetch_employee_count(symbol: str) -> dict[int, int]:
    """Best-effort annual employee head count."""
    out: dict[int, int] = {}
    try:
        df = ak.stock_employee_em(symbol=em_symbol(symbol))
        if df is None or df.empty:
            return out
        for _, row in df.iterrows():
            raw = str(row.get("报告期", row.get("报告年度", "")))
            year = extract_year(raw)
            emp = safe_float(
                row.get("员工总数", row.get("在职员工数量合计", row.get("员工人数")))
            )
            if year in YEARS and emp:
                out[year] = int(emp)
    except Exception as exc:
        log.debug("  [%s] employee n/a: %s", symbol, exc)
    return out


# ── MCU derivation ────────────────────────────────────────────────────────────

def apply_mcu_strategy(
    financials: dict[int, dict],
    meta: dict,
    known_mcu: dict,
) -> dict[int, dict]:
    strategy = meta.get("mcu_strategy", "unknown")
    multiplier = float(meta.get("mcu_multiplier") or 1.0)
    base_conf = meta.get("mcu_confidence", "low")

    for year, row in financials.items():
        key = str(year)
        # Manually entered data takes priority (supports both _yuan and legacy _musd)
        if key in known_mcu and isinstance(known_mcu[key], dict):
            k = known_mcu[key]
            yuan = k.get("mcu_revenue_yuan")
            musd = k.get("mcu_revenue_musd")
            if yuan is not None:
                row["mcu_revenue_yuan"] = yuan
                row["mcu_data_type"] = k.get("data_type", "reported")
                row["mcu_confidence"] = k.get("confidence", "high")
                row["mcu_source"] = k.get("source", "Manual entry")
                if k.get("mcu_gross_margin") is not None:
                    row["gross_margin_pct"] = round(k["mcu_gross_margin"] * 100, 2)
                continue
            elif musd is not None:
                row["mcu_revenue_yuan"] = musd * FX.get(year, 7.2) * 1_000_000
                row["mcu_data_type"] = k.get("data_type", "reported")
                row["mcu_confidence"] = k.get("confidence", "high")
                row["mcu_source"] = k.get("source", "Manual entry")
                continue

        # Derive from total revenue
        total = row.get("total_revenue_yuan")
        if strategy in ("total_revenue", "total_proxy") and total is not None:
            row["mcu_revenue_yuan"] = total * multiplier
            row["mcu_data_type"] = "derived"
            row["mcu_confidence"] = base_conf
            row["mcu_source"] = (
                "总营收×1.0（纯MCU口径）"
                if multiplier == 1.0
                else f"总营收×{multiplier}"
            )
        elif strategy == "segment_industrial":
            # 工业控制芯片口径：数据需从年报分产品表提取，无法从总收入自动推算
            # 有 known_mcu 手工录入时已在上方处理；此处标记为 pending 等待录入
            row["mcu_revenue_yuan"] = None
            row["mcu_data_type"] = "pending"
            row["mcu_confidence"] = base_conf
            row["mcu_source"] = "需从年报分产品表提取（stock_zygc_em 或 extract_mcu_segments.py）"
        else:
            row["mcu_revenue_yuan"] = None
            row["mcu_data_type"] = "unavailable"
            row["mcu_confidence"] = "na" if strategy == "na" else base_conf
            row["mcu_source"] = None

    return financials


# ── derived metrics ───────────────────────────────────────────────────────────

def compute_metrics(financials: dict[int, dict]) -> dict[int, dict]:
    years = sorted(financials)

    for i, yr in enumerate(years):
        row = financials[yr]
        prev = financials.get(years[i - 1]) if i > 0 else None

        rev = row.get("total_revenue_yuan")
        rd = row.get("rd_expense_yuan")
        mcu = row.get("mcu_revenue_yuan")

        row["rd_pct"] = round(rd / rev * 100, 1) if (rev and rd) else None
        row["mcu_weight_pct"] = round(mcu / rev * 100, 1) if (rev and mcu) else None

        prev_rev = prev.get("total_revenue_yuan") if prev else None
        prev_mcu = prev.get("mcu_revenue_yuan") if prev else None

        row["revenue_yoy_pct"] = (
            round((rev / prev_rev - 1) * 100, 1)
            if (rev and prev_rev and prev_rev != 0)
            else None
        )
        row["mcu_yoy_pct"] = (
            round((mcu / prev_mcu - 1) * 100, 1)
            if (mcu and prev_mcu and prev_mcu != 0)
            else None
        )

        # USD mirror fields
        row["total_revenue_musd"] = to_musd(rev, yr)
        row["net_income_musd"] = to_musd(row.get("net_income_yuan"), yr)
        row["rd_expense_musd"] = to_musd(rd, yr)
        row["mcu_revenue_musd"] = to_musd(mcu, yr)

        # Filing metadata (for UI pipeline strip)
        dt = row.get("mcu_data_type", "unavailable")
        row["filing_status"] = (
            "reported"  if dt == "reported"
            else "derived"   if dt == "derived"
            else "estimated" if mcu is not None
            else "pending"
        )
        row["filing_date"] = f"{yr}-04-30"
        row["data_coverage"] = round(
            sum(1 for k in ["total_revenue_yuan", "rd_expense_yuan", "mcu_revenue_yuan"]
                if row.get(k) is not None) / 3, 2
        )

    # CAGR over available revenue range
    rev_map = {
        y: financials[y]["total_revenue_yuan"]
        for y in years
        if financials[y].get("total_revenue_yuan")
    }
    if len(rev_map) >= 2:
        y0, y1 = min(rev_map), max(rev_map)
        n = y1 - y0
        cagr = round(((rev_map[y1] / rev_map[y0]) ** (1 / n) - 1) * 100, 1)
        cagr_label = f"CAGR {y0}–{y1}"
    else:
        cagr = None
        cagr_label = "N/A"

    for row in financials.values():
        row["cagr_pct"] = cagr
        row["cagr_label"] = cagr_label

    return financials


# ── main ──────────────────────────────────────────────────────────────────────

def process_symbol(symbol: str, meta: dict, known_mcu: dict) -> dict:
    log.info("Processing %s (%s)…", meta["name_cn"], symbol)

    fin = fetch_profit_sheet(symbol)
    log.info("  financial rows: %d", len(fin))

    emp = fetch_employee_count(symbol)
    for yr, cnt in emp.items():
        if yr in fin:
            fin[yr]["employee_count"] = cnt

    fin = apply_mcu_strategy(fin, meta, known_mcu)
    fin = compute_metrics(fin)

    # ── BigQuery sync (graceful no-op if BQ not configured) ──────────────────
    if bq_writer.is_available():
        written = 0
        for yr, row in fin.items():
            ok = bq_writer.write_financials(symbol, yr, row, meta, period="年报")
            if ok:
                written += 1
        log.info("  BQ: wrote %d/%d rows for %s", written, len(fin), symbol)
    # ─────────────────────────────────────────────────────────────────────────

    return {
        "meta": meta,
        "financials": {str(y): row for y, row in sorted(fin.items())},
    }


def main() -> None:
    companies_meta: dict = json.loads((HERE / "companies_meta.json").read_text())
    mcu_known: dict = json.loads((HERE / "mcu_known_data.json").read_text())

    # Optional: filter to a single symbol passed as CLI arg
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if target and target not in companies_meta:
        sys.exit(f"Unknown symbol: {target}. Valid: {list(companies_meta)}")

    # Load existing data.json to preserve any cached results
    out_path = HERE / "data.json"
    if out_path.exists():
        existing = json.loads(out_path.read_text())
    else:
        existing = {"companies": {}}

    output = {
        "generated_at": date.today().isoformat(),
        "years": YEARS,
        "companies": existing.get("companies", {}),
    }

    symbols = [target] if target else list(companies_meta)
    for symbol in symbols:
        meta = companies_meta[symbol]
        result = process_symbol(symbol, meta, mcu_known.get(symbol, {}))
        output["companies"][symbol] = result

    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    log.info("Wrote %s  (%d companies)", out_path, len(output["companies"]))


if __name__ == "__main__":
    main()
