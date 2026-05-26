#!/usr/bin/env python3
"""smart_sync.py — Orchestrate the full MCU data refresh pipeline.

Steps:
  1. fetch_mcu_data.py        — AKShare profit sheet + MCU derivation → data.json
  2. fetch_yjbb_quarterly.py  — 业绩报表 Q4 snapshots → gross margin + NI YoY → merge
  3. validate_data.py         — basic schema checks on data.json

Usage:
    python smart_sync.py                # full refresh (all 11 companies)
    python smart_sync.py 603986         # single-company refresh
    python smart_sync.py --no-bq        # skip BigQuery writes (useful in Cloud Shell)
    python smart_sync.py --commit       # git-commit data.json after successful sync
    python smart_sync.py --no-bq --commit 603986
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def run(cmd: list[str], desc: str, extra_env: dict | None = None) -> bool:
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"{'='*60}")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(cmd, cwd=HERE, env=env)
    if result.returncode != 0:
        print(f"[FAILED] {desc} (exit {result.returncode})")
        return False
    return True


def git_commit_data(symbol: str | None) -> None:
    """Commit data.json (and yjbb_quarterly.json) with a descriptive message."""
    files = ["data.json", "yjbb_quarterly.json"]
    existing = [f for f in files if (HERE / f).exists()]
    if not existing:
        print("[commit] Nothing to commit — data files not found.")
        return

    scope = symbol if symbol else "all"
    msg = f"data: refresh financials via AKShare ({scope})"

    subprocess.run(["git", "add"] + existing, cwd=HERE, check=False)
    result = subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=HERE, capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"[commit] {result.stdout.strip()}")
        # Push to current tracking branch
        push = subprocess.run(
            ["git", "push"], cwd=HERE, capture_output=True, text=True,
        )
        if push.returncode == 0:
            print(f"[push]   {push.stdout.strip() or 'ok'}")
        else:
            print(f"[push]   FAILED — run 'git push' manually\n{push.stderr.strip()}")
    elif "nothing to commit" in result.stdout + result.stderr:
        print("[commit] data.json unchanged — nothing to commit.")
    else:
        print(f"[commit] FAILED\n{result.stderr.strip()}")


def main() -> None:
    args = sys.argv[1:]

    no_bq  = "--no-bq"  in args
    commit = "--commit" in args
    args   = [a for a in args if a not in ("--no-bq", "--commit")]

    target = args  # remaining positional args = optional symbol filter

    extra_env = {"MCU_BQ_DISABLED": "1"} if no_bq else {}
    if no_bq:
        print("BigQuery writes disabled (--no-bq)")

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
        ok = run(cmd, desc, extra_env=extra_env)
        if not ok:
            sys.exit(1)

    print("\n✓  smart_sync complete — data.json is ready.")

    if commit:
        sym = target[0] if len(target) == 1 else None
        git_commit_data(sym)


if __name__ == "__main__":
    main()
