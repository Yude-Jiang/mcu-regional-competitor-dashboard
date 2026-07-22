#!/usr/bin/env python3
"""extract_mcu_q1_from_pdf.py — MCU revenue estimates from Q1 report PDFs + ratios.

All Q1 MCU figures are estimates (一季报通常无年报级分产品表):
  - PDF MD&A lines (极海微、智能电表芯片等) → estimated, higher confidence
  - total_proxy × Q1 revenue → estimated
  - Prior FY MCU/revenue share × Q1 total → estimated

Writes mcu_known_data.json keys 2025Q1 / 2026Q1; run fetch_2026q1_data.py after.

Usage:
    python download_reports.py --years 2025 2026 --categories category_yjdbg_szsh \\
        --out finance_reports_q1_2025   # run per year or use two dirs below
    python extract_mcu_q1_from_pdf.py
    python fetch_2026q1_data.py
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

try:
    import fitz
except ImportError:
    sys.exit("pip install pymupdf")

HERE = Path(__file__).parent
META_PATH = HERE / "companies_meta.json"
KNOWN_PATH = HERE / "mcu_known_data.json"
DATA_PATH = HERE / "data.json"

PERIOD_DIRS: dict[str, Path] = {
    "2025Q1": HERE / "finance_reports_q1_2025",
    "2026Q1": HERE / "finance_reports_q1",
}

# FY used for MCU/总营收 ratio when applying to each Q1 period
RATIO_FY_FOR_PERIOD = {"2025Q1": 2024, "2026Q1": 2025}

EST_TAG = "【估算】"

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


def pdf_text(symbol: str, root: Path) -> str:
    folders = list(root.glob(f"{symbol}_*"))
    if not folders:
        return ""
    pdfs = sorted(folders[0].glob("*.pdf"), key=lambda p: p.stat().st_size, reverse=True)
    if not pdfs:
        return ""
    doc = fitz.open(pdfs[0])
    text = "\n".join(doc[i].get_text() for i in range(doc.page_count))
    doc.close()
    return text


def extract_pdf_disclosed(symbol: str, text: str, period_key: str) -> dict | None:
    year = period_key[:4]
    flat = text.replace("\n", "")

    if symbol == "002180":
        m = re.search(r"极海微营业收入\s*([\d\.]+)\s*亿", flat)
        if m:
            yuan = round(float(m.group(1)) * 1e8, 2)
            return {
                "mcu_revenue_yuan": yuan,
                "mcu_gross_margin": None,
                "data_type": "estimated",
                "confidence": "medium",
                "source": (
                    f"{year}一季报正文：极海微营业收入（子公司口径，非年报分产品表）"
                    f"{EST_TAG}"
                ),
            }

    if symbol == "688385":
        m = re.search(r"智能电表芯片约为\s*([\d\.]+)\s*亿", flat)
        if m:
            yuan = round(float(m.group(1)) * 1e8, 2)
            return {
                "mcu_revenue_yuan": yuan,
                "mcu_gross_margin": None,
                "data_type": "estimated",
                "confidence": "high",
                "source": (
                    f"{year}一季报经营讨论：智能电表芯片产品线收入（非完整分产品表）"
                    f"{EST_TAG}"
                ),
            }

    return None


def fy_mcu_ratio(symbol: str, data: dict, fy: int) -> float | None:
    fin = (data.get("companies", {}).get(symbol, {}) or {}).get("financials", {})
    row = fin.get(str(fy)) or {}
    rev = row.get("total_revenue_yuan")
    mcu = row.get("mcu_revenue_yuan")
    if rev and mcu and rev > 0:
        return mcu / rev
    known = json.loads(KNOWN_PATH.read_text()).get(symbol, {}).get(str(fy))
    if isinstance(known, dict) and known.get("mcu_revenue_yuan") and rev:
        return known["mcu_revenue_yuan"] / rev
    return None


def q1_total_from_data(data: dict, symbol: str, period_key: str) -> float | None:
    if period_key == "2026Q1":
        row = (data.get("companies", {}).get(symbol, {}) or {}).get("financials", {}).get(
            "2026Q1", {}
        )
        return row.get("total_revenue_yuan")
    import fetch_2026q1_data as fq

    pl = fq.fetch_q1_pl(symbol, fq.Q1_2025_END)
    return pl.get("total_revenue_yuan") if pl else None


def strategy_row(
    symbol: str,
    meta: dict,
    period_key: str,
    q1_total: float | None,
    text: str,
    ratio: float | None,
) -> dict | None:
    year = period_key[:4]
    strat = meta.get("mcu_strategy", "")
    mult = float(meta.get("mcu_multiplier") or 1.0)

    if strat == "subsidiary_geehy":
        return extract_pdf_disclosed(symbol, text, period_key)

    if strat == "total_proxy" and q1_total:
        mcu = round(q1_total * mult, 2)
        return {
            "mcu_revenue_yuan": mcu,
            "mcu_gross_margin": None,
            "data_type": "estimated",
            "confidence": meta.get("mcu_confidence", "high"),
            "source": (
                f"{year}一季报：总营收×{mult}（纯MCU公司近似，无分产品披露）{EST_TAG}"
            ),
        }

    disclosed = extract_pdf_disclosed(symbol, text, period_key)
    if disclosed:
        return disclosed

    if q1_total and ratio:
        fy = RATIO_FY_FOR_PERIOD[period_key]
        mcu = round(q1_total * ratio, 2)
        return {
            "mcu_revenue_yuan": mcu,
            "mcu_gross_margin": None,
            "data_type": "estimated",
            "confidence": "low",
            "source": (
                f"{year}一季报未披露MCU分产品；按FY{fy} MCU/总营收占比×Q1合并营收{EST_TAG}"
            ),
        }

    return None


def process_period(period_key: str, known: dict, data: dict, meta_all: dict) -> int:
    root = PERIOD_DIRS[period_key]
    if not root.is_dir():
        log.warning("Missing PDF dir %s — skip %s", root, period_key)
        return 0
    n = 0
    for symbol, meta in meta_all.items():
        text = pdf_text(symbol, root)
        q1_total = q1_total_from_data(data, symbol, period_key)
        ratio = fy_mcu_ratio(symbol, data, RATIO_FY_FOR_PERIOD[period_key])
        entry = strategy_row(symbol, meta, period_key, q1_total, text, ratio)
        if not entry:
            log.warning("[%s] no MCU for %s", symbol, period_key)
            continue
        known.setdefault(symbol, {})[period_key] = entry
        n += 1
        log.info("[%s] %s %s MCU=%.0f", symbol, period_key, entry["data_type"], entry["mcu_revenue_yuan"])
    return n


def main() -> int:
    if not KNOWN_PATH.exists() or not DATA_PATH.exists():
        log.error("Need mcu_known_data.json and data.json")
        return 1

    meta_all = json.loads(META_PATH.read_text())
    known = json.loads(KNOWN_PATH.read_text())
    data = json.loads(DATA_PATH.read_text())

    total = 0
    for period_key in ("2025Q1", "2026Q1"):
        total += process_period(period_key, known, data, meta_all)

    KNOWN_PATH.write_text(json.dumps(known, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info("Wrote %s (%d period-rows)", KNOWN_PATH, total)

    import fetch_2026q1_data

    return fetch_2026q1_data.main()


if __name__ == "__main__":
    sys.exit(main())
