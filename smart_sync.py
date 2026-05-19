#!/usr/bin/env python3
"""smart_sync.py — Orchestrate the full MCU data refresh pipeline.

Steps:
  1. fetch_mcu_data.py        — AKShare profit sheet + MCU derivation → data.json
  2. fetch_yjbb_quarterly.py  — 业绩报表 Q4 snapshots → gross margin + NI YoY → merge
  3. validate_data.py         — basic schema checks on data.json

Usage:
    python smart_sync.py            # full refresh (all 11 companies)
    python smart_sync.py 603986     # single-company profit-sheet refresh only
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def run(cmd: list[str], desc: str) -> bool:
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=HERE)
    if result.returncode != 0:
        print(f"[FAILED] {desc} (exit {result.returncode})")
        return False
    return True


def main() -> None:
    target = sys.argv[1:]  # optional symbol filter, e.g. ["603986"]

    steps = [
        (
            [sys.executable, "fetch_mcu_data.py"] + target,
            "Step 1 — Profit sheet (股票利润表) + MCU derivation → data.json",
        ),
        (
            [sys.executable, "fetch_yjbb_quarterly.py"],
            "Step 2 — 业绩报表 Q4 snapshots (gross margin, NI YoY) → merge into data.json",
        ),
        (
            [sys.executable, "validate_data.py"],
            "Step 3 — Validate data.json schema",
        ),
    ]

    for cmd, desc in steps:
        ok = run(cmd, desc)
        if not ok:
            sys.exit(1)

    print("\n✓  smart_sync complete — data.json is ready.")


if __name__ == "__main__":
    main()
