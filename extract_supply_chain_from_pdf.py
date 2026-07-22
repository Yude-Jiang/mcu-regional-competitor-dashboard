#!/usr/bin/env python3
"""extract_supply_chain_from_pdf.py — Heuristic supply-chain hints from local CNINFO annual PDFs.

Reads PDFs under finance_reports/ (from download_reports.py) and emits machine-readable
snippets to help maintain docs/supply_chain_draft.json. Output is NOT authoritative;
curate quotes into supply_chain_draft.json after review.

Usage:
    python download_reports.py --years 2024 2025 --out finance_reports
    python extract_supply_chain_from_pdf.py --out /tmp/supply_chain_hints.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import fitz

from download_reports import COMPANY_INFO

HERE = Path(__file__).parent
DEFAULT_REPORTS = HERE / "finance_reports"

FOUNDRY_PATTERNS: list[tuple[str, str]] = [
    ("TSMC", r"台积电|TSMC"),
    ("SMIC", r"中芯国际|中芯(?!华)"),
    ("HHGrace", r"华虹(?:宏力|半导体)?"),
    ("HLMC", r"华力|合肥晶合|晶合集成"),
    ("GF", r"格罗方德|格芯|GlobalFoundries"),
    ("UMC", r"联华电子|联电|和舰"),
    ("LXCC", r"联芯集成电路"),
]
OSAT_PATTERNS: list[tuple[str, str]] = [
    ("JCET", r"长电科技|江苏长电"),
    ("TFME", r"通富微电|通富"),
    ("Huatian", r"华天科技|天水华天"),
    ("ASE", r"日月光"),
    ("SJSemi", r"盛合晶微"),
    ("WeTest", r"伟测科技"),
    ("Unimos", r"紫光宏茂"),
    ("KYEC", r"京隆科技|京元"),
]


def _match_codes(text: str, patterns: list[tuple[str, str]]) -> list[str]:
    found: list[str] = []
    for code, pat in patterns:
        if re.search(pat, text):
            if code not in found:
                found.append(code)
    return found


def _mcu_process_line(text: str) -> str | None:
    for ln in text.split("\n"):
        if "MCU" in ln and ("纳米" in ln or "nm" in ln or "制程" in ln):
            if re.search(r"\d{2}\s*(?:纳米|nm)|110nm|55nm|40nm|22nm", ln, re.I):
                return ln.strip()[:300]
    return None


def _mcu_nodes_from_text(text: str) -> list[str]:
    nodes: set[str] = set()
    for seg in re.findall(r"(?:MCU|微控制器)[^。\n]{0,250}", text):
        for n in re.findall(r"(\d{2,3})\s*(?:纳米|nm)", seg, re.I):
            if 10 <= int(n) <= 180:
                nodes.add(str(int(n)))
        for n in re.findall(r"110nm|55nm|40nm|22nm|90nm", seg, re.I):
            nodes.add(re.sub(r"nm", "", n, flags=re.I))
    return sorted(nodes, key=lambda x: int(x))


def analyze_pdf(pdf_path: Path) -> dict:
    doc = fitz.open(pdf_path)
    text = "\n".join(doc[i].get_text() for i in range(len(doc)))
    doc.close()

    return {
        "pdf": pdf_path.name,
        "foundry_fab_auto": _match_codes(text, FOUNDRY_PATTERNS),
        "backend_fab_auto": _match_codes(text, OSAT_PATTERNS),
        "mcu_process_line": _mcu_process_line(text),
        "process_nodes_mcu_auto": _mcu_nodes_from_text(text),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", default=str(DEFAULT_REPORTS))
    parser.add_argument("--years", nargs="*", type=int, default=[2024, 2025])
    parser.add_argument("--out", default="-", help="JSON path or - for stdout")
    args = parser.parse_args()

    root = Path(args.reports)
    out: dict = {"years": args.years, "companies": {}}

    for sym in COMPANY_INFO:
        folder = next(root.glob(f"{sym}_*"), None)
        if not folder:
            continue
        out["companies"][sym] = {"name": COMPANY_INFO[sym]["name"], "by_year": {}}
        for year in args.years:
            pdfs = sorted(folder.glob(f"*{year}*"))
            if not pdfs:
                continue
            out["companies"][sym]["by_year"][str(year)] = analyze_pdf(pdfs[0])

    payload = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(payload)
    else:
        Path(args.out).write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    main()
