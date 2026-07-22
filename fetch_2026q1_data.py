#!/usr/bin/env python3
"""fetch_2026q1_data.py — Pull Q1 2026 (and Q1 2025 for YoY) from AKShare into data.json.

Adds financials['2026Q1'] per company. MCU segment revenue is usually absent in
East Money quarterly feeds — mcu_revenue_yuan stays null unless manually added later.

Usage:
    python fetch_2026q1_data.py
    python validate_data.py
"""

from __future__ import annotations

import json
import logging
import math
import sys
from datetime import date
from pathlib import Path

try:
    import akshare as ak
except ImportError as e:
    sys.exit(f"Missing dependency: {e}\nInstall: pip install akshare pandas")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HERE = Path(__file__).parent
PERIOD_KEY = "2026Q1"
Q1_2026_END = "2026-03-31"
Q1_2025_END = "2025-03-31"
FX_YEAR = 2026

_fx_path = HERE / "fx_rates.json"
FX: dict[int, float] = (
    {int(k): v for k, v in json.loads(_fx_path.read_text())["CNY_USD"].items()}
    if _fx_path.exists()
    else {2026: 7.25}
)


def em_symbol(code: str) -> str:
    return ("SH" if code.startswith("6") else "SZ") + code


def safe_float(v) -> float | None:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def row_at(df, date_end: str) -> dict | None:
    if df is None or df.empty:
        return None
    for _, row in df.iterrows():
        d = str(row.get("REPORT_DATE", ""))
        if date_end in d:
            return row
    return None


def parse_pl_row(row) -> dict:
    revenue = safe_float(row.get("TOTAL_OPERATE_INCOME") or row.get("OPERATE_INCOME"))
    net_inc = safe_float(row.get("PARENT_NETPROFIT") or row.get("NETPROFIT"))
    rd = safe_float(row.get("RESEARCH_EXPENSE") or row.get("ME_RESEARCH_EXPENSE"))
    cost = safe_float(row.get("OPERATE_COST") or row.get("TOTAL_OPERATE_COST"))
    gm = None
    if revenue and cost is not None and revenue > 0:
        gm = round((revenue - cost) / revenue * 100, 2)
    return {
        "total_revenue_yuan": revenue,
        "net_income_yuan": net_inc,
        "rd_expense_yuan": rd,
        "gross_margin_pct": gm,
    }


def to_musd(yuan: float | None) -> float | None:
    if yuan is None:
        return None
    fx = FX.get(FX_YEAR, 7.25)
    return round(yuan / fx / 1_000_000, 2)


def build_q1_row(cur: dict, prev: dict | None, meta: dict) -> dict:
    rev = cur.get("total_revenue_yuan")
    rd = cur.get("rd_expense_yuan")
    prev_rev = (prev or {}).get("total_revenue_yuan")
    prev_net = (prev or {}).get("net_income_yuan")

    row = {
        **cur,
        "mcu_revenue_yuan": None,
        "mcu_data_type": "unavailable",
        "mcu_confidence": meta.get("mcu_confidence", "na"),
        "mcu_source": "一季报未披露分产品 MCU 收入（仅合并利润表口径）",
        "rd_pct": round(rd / rev * 100, 1) if (rev and rd) else None,
        "mcu_weight_pct": None,
        "revenue_yoy_pct": (
            round((rev / prev_rev - 1) * 100, 1)
            if (rev and prev_rev and prev_rev != 0)
            else None
        ),
        "mcu_yoy_pct": None,
        "net_income_yoy_pct": (
            round((cur.get("net_income_yuan") / prev_net - 1) * 100, 1)
            if (cur.get("net_income_yuan") is not None and prev_net and prev_net != 0)
            else None
        ),
        "fx_rate_cny_usd": FX.get(FX_YEAR),
        "total_revenue_musd": to_musd(rev),
        "net_income_musd": to_musd(cur.get("net_income_yuan")),
        "rd_expense_musd": to_musd(rd),
        "mcu_revenue_musd": None,
        "filing_status": "q1_reported",
        "filing_date": "2026-04-30",
        "period_end": Q1_2026_END,
        "period_label": "2026年一季度",
        "data_coverage": round(
            sum(1 for k in ("total_revenue_yuan", "rd_expense_yuan") if cur.get(k) is not None) / 3,
            2,
        ),
        "cagr_pct": None,
        "cagr_label": "N/A (单季)",
        "employee_count": None,
    }

    # 纳思达：AKShare 为集团合并口径，不在此写入总营收（与年报策略一致）
    if meta.get("mcu_strategy") == "subsidiary_geehy":
        row["total_revenue_yuan"] = None
        row["total_revenue_musd"] = None
        row["net_income_yuan"] = None
        row["net_income_musd"] = None
        row["gross_margin_pct"] = None
        row["revenue_yoy_pct"] = None
        row["net_income_yoy_pct"] = None
        row["mcu_source"] = "合并一季报为集团口径，极海 MCU 需子公司/人工数据"

    # 中微半导等纯 MCU：合并毛利率可近似展示
    if meta.get("mcu_strategy") in ("total_proxy", "total_revenue") and row.get("gross_margin_pct"):
        pass  # keep consolidated GM as proxy

    return row


def fetch_symbol(symbol: str, meta: dict) -> dict | None:
    sym = em_symbol(symbol)
    try:
        df = ak.stock_profit_sheet_by_report_em(symbol=sym)
    except Exception as exc:
        log.warning("[%s] profit_sheet failed: %s", symbol, exc)
        return None

    r26 = row_at(df, Q1_2026_END)
    r25 = row_at(df, Q1_2025_END)
    if r26 is None:
        log.warning("[%s] no row for %s", symbol, Q1_2026_END)
        return None

    cur = parse_pl_row(r26)
    prev = parse_pl_row(r25) if r25 is not None else None
    return build_q1_row(cur, prev, meta)


def main() -> int:
    meta_all = json.loads((HERE / "companies_meta.json").read_text())
    data_path = HERE / "data.json"
    if not data_path.exists():
        log.error("data.json missing — run fetch_mcu_data.py first")
        return 1

    data = json.loads(data_path.read_text())
    companies = data.setdefault("companies", {})
    ok = 0

    for symbol, meta in meta_all.items():
        row = fetch_symbol(symbol, meta)
        if not row:
            continue
        co = companies.setdefault(symbol, {"meta": meta, "financials": {}})
        if "meta" not in co or not co["meta"]:
            co["meta"] = meta
        co.setdefault("financials", {})[PERIOD_KEY] = row
        ok += 1
        log.info(
            "[%s] %s rev=%s yoy=%s%%",
            symbol,
            meta.get("name_cn"),
            row.get("total_revenue_yuan"),
            row.get("revenue_yoy_pct"),
        )

    years = data.get("years") or list(range(2018, 2026))
    if PERIOD_KEY not in years:
        years = list(years) + [PERIOD_KEY]
    data["years"] = years
    data["periods"] = data.get("periods") or {}
    data["periods"][PERIOD_KEY] = {
        "label_zh": "2026年一季报",
        "label_en": "2026 Q1 Report",
        "period_end": Q1_2026_END,
        "type": "quarter",
    }
    data["generated_at"] = date.today().isoformat()
    data["q1_updated_at"] = date.today().isoformat()

    data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    log.info("Wrote %s — %d/11 companies with %s", data_path, ok, PERIOD_KEY)
    return 0 if ok >= 10 else 1


if __name__ == "__main__":
    sys.exit(main())
