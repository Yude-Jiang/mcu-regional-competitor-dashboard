#!/usr/bin/env python3
"""extract_mcu_q1_2026.py — MCU revenue (and GM when stated) from 2026 Q1 report PDFs.

Most A-share Q1 reports omit the annual 分产品表. This script:
  1) Parses MD&A sentences (e.g. 极海微营收、智能电表芯片收入)
  2) Applies companies_meta mcu_strategy (total_proxy, subsidiary_geehy)
  3) Falls back to FY2025 MCU/总营收占比 × Q1 合并营收 (derived, medium)

Writes mcu_known_data.json keys "2026Q1" and refreshes data.json via fetch_2026q1_data.

Usage:
    python download_reports.py --years 2026 --categories category_yjdbg_szsh \\
        --out finance_reports_q1
    python extract_mcu_q1_2026.py
    python fetch_2026q1_data.py
    python validate_data.py
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from pathlib import Path

try:
    import fitz  # pymupdf
except ImportError:
    sys.exit("pip install pymupdf")

HERE = Path(__file__).parent
PERIOD = "2026Q1"
Q1_PDF_ROOT = HERE / "finance_reports_q1"
META_PATH = HERE / "companies_meta.json"
KNOWN_PATH = HERE / "mcu_known_data.json"
DATA_PATH = HERE / "data.json"

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


def pdf_text(symbol: str) -> str:
    folders = list(Q1_PDF_ROOT.glob(f"{symbol}_*"))
    if not folders:
        return ""
    pdfs = list(folders[0].glob("*.pdf"))
    if not pdfs:
        return ""
    doc = fitz.open(pdfs[0])
    text = "\n".join(doc[i].get_text() for i in range(doc.page_count))
    doc.close()
    return text


def parse_yi_yuan(snippet: str) -> float | None:
    """Parse Chinese 3.19 亿元 / 1.23亿元 / 12,345.67 万元 → CNY yuan."""
    s = snippet.replace(",", "").replace(" ", "")
    m = re.search(r"([\d\.]+)\s*亿", s)
    if m:
        return round(float(m.group(1)) * 1e8, 2)
    m = re.search(r"([\d\.]+)\s*万", s)
    if m:
        return round(float(m.group(1)) * 1e4, 2)
    return None


def extract_pdf_disclosed(symbol: str, text: str) -> dict | None:
  """Return mcu_known_data-style dict if Q1正文 explicitly states MCU-related revenue."""
  if symbol == "002180":
    m = re.search(
        r"极海微营业收入\s*([\d\.]+)\s*亿",
        text.replace("\n", ""),
    )
    if m:
      yuan = round(float(m.group(1)) * 1e8, 2)
      return {
          "mcu_revenue_yuan": yuan,
          "mcu_gross_margin": None,
          "data_type": "reported",
          "confidence": "medium",
          "source": "2026一季报 极海微营业收入（子公司合并口径，含工控/汽车/主控等）",
      }

  if symbol == "688385":
    m = re.search(
        r"智能电表芯片约为\s*([\d\.]+)\s*亿",
        text.replace("\n", ""),
    )
    if m:
      yuan = round(float(m.group(1)) * 1e8, 2)
      return {
          "mcu_revenue_yuan": yuan,
          "mcu_gross_margin": None,
          "data_type": "reported",
          "confidence": "high",
          "source": "2026一季报 经营情况讨论 智能电表芯片产品线收入",
      }

  return None


def fy2025_ratio(symbol: str, data: dict) -> float | None:
    fin = (data.get("companies", {}).get(symbol, {}) or {}).get("financials", {})
    row25 = fin.get("2025") or {}
    rev = row25.get("total_revenue_yuan")
    mcu = row25.get("mcu_revenue_yuan")
    if rev and mcu and rev > 0:
        return mcu / rev
    known = json.loads(KNOWN_PATH.read_text()).get(symbol, {}).get("2025")
    if isinstance(known, dict) and known.get("mcu_revenue_yuan") and rev:
        return known["mcu_revenue_yuan"] / rev
    return None


def strategy_row(
    symbol: str,
    meta: dict,
    q1_total: float | None,
    text: str,
    ratio: float | None,
) -> dict | None:
    strat = meta.get("mcu_strategy", "")
    mult = float(meta.get("mcu_multiplier") or 1.0)

    if strat == "subsidiary_geehy":
        return extract_pdf_disclosed(symbol, text)

    if strat == "total_proxy" and q1_total:
        mcu = round(q1_total * mult, 2)
        return {
            "mcu_revenue_yuan": mcu,
            "mcu_gross_margin": None,
            "data_type": "derived",
            "confidence": meta.get("mcu_confidence", "high"),
            "source": f"2026一季报 总营收×{mult}（纯MCU公司近似）",
        }

    disclosed = extract_pdf_disclosed(symbol, text)
    if disclosed:
        return disclosed

    if q1_total and ratio:
        mcu = round(q1_total * ratio, 2)
        return {
            "mcu_revenue_yuan": mcu,
            "mcu_gross_margin": None,
            "data_type": "derived",
            "confidence": "medium",
            "source": "2026一季报未披露分产品MCU；按FY2025 MCU/总营收占比×Q1合并营收",
        }

    return None


def main() -> int:
    if not KNOWN_PATH.exists() or not DATA_PATH.exists():
        log.error("Need mcu_known_data.json and data.json")
        return 1

    meta_all = json.loads(META_PATH.read_text())
    known = json.loads(KNOWN_PATH.read_text())
    data = json.loads(DATA_PATH.read_text())

    updated = 0
    for symbol, meta in meta_all.items():
        text = pdf_text(symbol)
        q1_row = (data.get("companies", {}).get(symbol, {}) or {}).get("financials", {}).get(PERIOD, {})
        q1_total = q1_row.get("total_revenue_yuan")
        ratio = fy2025_ratio(symbol, data)

        entry = strategy_row(symbol, meta, q1_total, text, ratio)
        if not entry:
            log.warning("[%s] no MCU Q1 estimate", symbol)
            continue

        known.setdefault(symbol, {})[PERIOD] = entry
        updated += 1
        log.info(
            "[%s] %s MCU=%.0f  %s",
            symbol,
            meta.get("name_cn"),
            entry["mcu_revenue_yuan"],
            entry["data_type"],
        )

    KNOWN_PATH.write_text(json.dumps(known, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info("Updated %s (%d symbols with %s)", KNOWN_PATH, updated, PERIOD)

    # Re-merge into data.json
    import fetch_2026q1_data

    return fetch_2026q1_data.main()


if __name__ == "__main__":
    sys.exit(main())
