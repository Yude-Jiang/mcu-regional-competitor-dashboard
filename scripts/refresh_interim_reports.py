#!/usr/bin/env python3
"""Orchestrate AKShare refresh for interim periods (Q1/H1).

Used by .github/workflows/refresh-interim-data.yml:
  python scripts/refresh_interim_reports.py --period h1 --write-summary

Writes GitHub Actions outputs when GITHUB_OUTPUT is set:
  changed=true|false
  new_disclosures=["300327", ...]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

PERIOD_CONFIG = {
    "q1": ("fetch_2026q1_data.py", "2026Q1"),
    "h1": ("fetch_2026h1_data.py", "2026H1"),
}

KNOWN_SYMBOLS = [
    "603986", "300327", "688380", "300077", "688279",
    "002180", "688385", "688766", "688595", "688391", "688018",
]


def load_data(path: Path) -> dict:
    return json.loads(path.read_text())


def period_status(data: dict, period_key: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for sym in KNOWN_SYMBOLS:
        co = data.get("companies", {}).get(sym, {})
        row = (co.get("financials") or {}).get(period_key) or {}
        meta = co.get("meta") or {}
        out[sym] = {
            "name_cn": meta.get("name_cn", sym),
            "filing_status": row.get("filing_status"),
            "total_revenue_yuan": row.get("total_revenue_yuan"),
            "mcu_revenue_yuan": row.get("mcu_revenue_yuan"),
        }
    return out


def classify(status: dict[str, dict]) -> tuple[list[str], list[str]]:
    reported, pending = [], []
    for sym, row in status.items():
        fs = row.get("filing_status")
        if fs == "pending":
            pending.append(sym)
        elif row.get("total_revenue_yuan") is not None or fs in (
            "q1_reported",
            "h1_reported",
            "estimated",
        ):
            reported.append(sym)
        else:
            pending.append(sym)
    return reported, pending


def fmt_yi(yuan: float | None) -> str:
    if yuan is None:
        return "—"
    return f"{yuan / 1e8:.2f}亿"


def write_github_summary(
    period_keys: list[str],
    before: dict[str, dict[str, dict]],
    after: dict[str, dict[str, dict]],
    newly: list[str],
    changed: bool,
) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    lines = ["## Interim data refresh\n"]
    for pk in period_keys:
        rep, pend = classify(after[pk])
        lines.append(f"### {pk}\n")
        lines.append(f"- **Reported:** {len(rep)}/11")
        lines.append(f"- **Pending:** {len(pend)}/11")
        if pend:
            names = [f"{s} {after[pk][s]['name_cn']}" for s in pend]
            lines.append(f"- Pending symbols: {', '.join(names)}")
        if newly:
            lines.append("\n**Newly disclosed this run:**\n")
            for sym in newly:
                b = before[pk].get(sym, {})
                a = after[pk][sym]
                lines.append(
                    f"- `{sym}` {a['name_cn']}: "
                    f"rev {fmt_yi(a.get('total_revenue_yuan'))}, "
                    f"MCU {fmt_yi(a.get('mcu_revenue_yuan'))} "
                    f"(was {b.get('filing_status', 'missing')})"
                )
        lines.append("")

    lines.append(f"**data.json changed:** {'yes' if changed else 'no'}\n")
    Path(summary_path).write_text("\n".join(lines), encoding="utf-8")


def git_data_changed() -> bool:
    r = subprocess.run(
        ["git", "diff", "--quiet", "data.json"],
        cwd=HERE,
        capture_output=True,
    )
    return r.returncode != 0


def run_fetch(script: str) -> None:
    path = HERE / script
    subprocess.run([sys.executable, str(path)], cwd=HERE, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh interim report data from AKShare")
    parser.add_argument(
        "--period",
        choices=["h1", "q1", "both"],
        default="h1",
        help="Which interim period(s) to refresh (default: h1)",
    )
    parser.add_argument(
        "--write-summary",
        action="store_true",
        help="Write job summary to GITHUB_STEP_SUMMARY when set",
    )
    args = parser.parse_args()

    data_path = HERE / "data.json"
    if not data_path.exists():
        print("data.json missing", file=sys.stderr)
        return 1

    period_keys: list[str] = []
    if args.period in ("q1", "both"):
        period_keys.append("2026Q1")
    if args.period in ("h1", "both"):
        period_keys.append("2026H1")

    before_data = load_data(data_path)
    before = {pk: period_status(before_data, pk) for pk in period_keys}

    if args.period in ("q1", "both"):
        run_fetch(PERIOD_CONFIG["q1"][0])
    if args.period in ("h1", "both"):
        run_fetch(PERIOD_CONFIG["h1"][0])

    subprocess.run([sys.executable, str(HERE / "validate_data.py")], cwd=HERE, check=True)

    after_data = load_data(data_path)
    after = {pk: period_status(after_data, pk) for pk in period_keys}

    newly: list[str] = []
    for pk in period_keys:
        for sym in KNOWN_SYMBOLS:
            b = before[pk].get(sym, {})
            a = after[pk][sym]
            if b.get("filing_status") == "pending" and a.get("filing_status") != "pending":
                if a.get("total_revenue_yuan") is not None or a.get("filing_status") in ("h1_reported", "q1_reported", "estimated"):
                    newly.append(sym)

    newly = sorted(set(newly))
    changed = git_data_changed()

    if args.write_summary:
        write_github_summary(period_keys, before, after, newly, changed)

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")
            fh.write(f"new_disclosures={json.dumps(newly, ensure_ascii=False)}\n")

    for pk in period_keys:
        rep, pend = classify(after[pk])
        print(f"{pk}: reported={len(rep)}/11 pending={len(pend)}/11")
        if pend:
            print(f"  pending: {', '.join(pend)}")
    print(f"new_disclosures: {newly or 'none'}")
    print(f"data.json changed: {changed}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
