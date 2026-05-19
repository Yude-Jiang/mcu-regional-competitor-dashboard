#!/usr/bin/env python3
"""smart_sync.py — Orchestrate the full MCU data refresh pipeline.

Steps:
  1. fetch_mcu_data.py  — AKShare fetch + MCU derivation → data.json
  2. validate_data.py   — basic schema checks on data.json

Usage:
    python smart_sync.py            # full refresh (all 11 companies)
    python smart_sync.py 603986     # single-company refresh
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
            "Fetch financials via AKShare + apply MCU derivation rules",
        ),
        (
            [sys.executable, "validate_data.py"],
            "Validate data.json schema",
        ),
    ]

    for cmd, desc in steps:
        ok = run(cmd, desc)
        if not ok:
            sys.exit(1)

    print("\n✓  smart_sync complete — data.json is ready.")


if __name__ == "__main__":
    main()
