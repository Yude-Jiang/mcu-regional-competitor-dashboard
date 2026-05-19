#!/usr/bin/env python3
"""upload_pdfs.py — Scan locally downloaded PDFs → upload to GCS → index in BigQuery.

Idempotent: files already recorded in pdf_index are skipped.

Usage:
    python upload_pdfs.py                       # scan default LOCAL_DIR
    python upload_pdfs.py /path/to/reports      # custom local dir
    python upload_pdfs.py --dry-run             # show what would be uploaded

Environment variables (override defaults):
    GCP_PROJECT          required
    GCS_BUCKET           default: mcu-annual-reports
    LOCAL_REPORTS_DIR    default: ./downloaded_reports
"""

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from google.cloud import storage
except ImportError:
    sys.exit("Missing dep: pip install google-cloud-storage")

import bq_writer

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT   = (os.environ.get("GCP_PROJECT")
             or os.environ.get("GOOGLE_CLOUD_PROJECT")
             or "st-china-ai-force")
BUCKET    = os.environ.get("GCS_BUCKET", "st-finance-reports")

STOCK_MAP = {
    "603986": "兆易创新",
    "300327": "中颖电子",
    "688380": "中微半导",
    "300077": "国民技术",
    "688279": "峰岹科技",
    "002180": "纳思达",
    "688385": "复旦微电",
    "688766": "普冉股份",
    "688595": "芯海科技",
    "688391": "钜泉科技",
    "688018": "乐鑫科技",
}

PERIOD_KEYWORDS = {
    "年报":   ["年度报告", "年报"],
    "一季报": ["第一季度报告", "一季报"],
    "半年报": ["半年度报告", "半年报"],
    "三季报": ["第三季度报告", "三季报"],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def detect_period(filename: str) -> str:
    for period, keywords in PERIOD_KEYWORDS.items():
        for kw in keywords:
            if kw in filename:
                return period
    return "年报"


def detect_year(filename: str) -> int | None:
    m = re.search(r"(20\d{2})", filename)
    return int(m.group(1)) if m else None


def detect_stock_id(folder_name: str) -> str | None:
    """Extract 6-digit stock code from folder name like '603986_兆易创新'."""
    m = re.match(r"^(\d{6})", folder_name)
    return m.group(1) if m and m.group(1) in STOCK_MAP else None


def already_indexed(stock_id: str, year: int, period: str) -> bool:
    """Check BigQuery pdf_index for an existing record."""
    status = bq_writer.get_doc_status_matrix()
    return bool(
        status.get(stock_id, {})
              .get(str(year), {})
              .get(period)
    )


def upload_to_gcs(local_path: str, gcs_key: str, dry_run: bool) -> bool:
    if dry_run:
        return True
    if not PROJECT:
        print("    ⚠  GCP_PROJECT not set — skipping GCS upload")
        return False
    try:
        gcs = storage.Client(project=PROJECT)
        bucket = gcs.bucket(BUCKET)
        blob = bucket.blob(gcs_key)
        blob.upload_from_filename(local_path, timeout=180)
        return True
    except Exception as exc:
        print(f"    GCS upload failed: {exc}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Upload local PDFs to GCS and index in BigQuery")
    parser.add_argument("local_dir", nargs="?",
                        default=os.environ.get("LOCAL_REPORTS_DIR", "./downloaded_reports"),
                        help="Root directory of downloaded PDFs")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be uploaded without doing it")
    args = parser.parse_args()

    local_dir = Path(args.local_dir)
    if not local_dir.exists():
        sys.exit(
            f"Directory not found: {local_dir}\n"
            f"Set LOCAL_REPORTS_DIR or pass path as argument.\n"
            f"Expected structure:\n"
            f"  {local_dir}/\n"
            f"    603986_兆易创新/\n"
            f"      603986_兆易创新_2024年报_兆易创新2024年年度报告.pdf\n"
        )

    print(f"\n{'='*60}")
    print(f"PDF → GCS Upload {'[DRY RUN] ' if args.dry_run else ''}")
    print(f"Local : {local_dir}")
    print(f"GCS   : gs://{BUCKET}/reports/")
    print(f"BQ    : {'available' if bq_writer.is_available() else 'offline (index skipped)'}")
    print(f"{'='*60}\n")

    success, skipped, failed = [], [], []

    for folder in sorted(local_dir.iterdir()):
        if not folder.is_dir():
            continue
        stock_id = detect_stock_id(folder.name)
        if not stock_id:
            print(f"⚠  Unknown folder '{folder.name}' — skipped")
            continue

        stock_name = STOCK_MAP[stock_id]
        pdfs = sorted(folder.glob("*.pdf")) + sorted(folder.glob("*.PDF"))
        if not pdfs:
            continue

        print(f"▶ {stock_name} ({stock_id})  — {len(pdfs)} file(s)")

        for pdf_path in pdfs:
            year   = detect_year(pdf_path.name)
            period = detect_period(pdf_path.name)

            if year is None:
                print(f"  ⚠  Cannot detect year: {pdf_path.name} — skipped")
                failed.append(str(pdf_path.name))
                continue

            # Idempotency check
            if not args.dry_run and already_indexed(stock_id, year, period):
                print(f"  ↷ {year}{period}  already indexed — skipped")
                skipped.append(pdf_path.name)
                continue

            file_size_kb = pdf_path.stat().st_size // 1024
            # GCS key: reports/{stock_id}_{name}/{year}_{period}_{original_name}
            gcs_key  = f"reports/{stock_id}_{stock_name}/{year}_{period}_{pdf_path.name}"
            gcs_uri  = f"gs://{BUCKET}/{gcs_key}"

            print(f"  ↑ {year}{period}  {file_size_kb:,} KB  → {gcs_key[:60]}…",
                  end="  ", flush=True)

            ok = upload_to_gcs(str(pdf_path), gcs_key, args.dry_run)
            if not ok:
                print("FAILED")
                failed.append(pdf_path.name)
                continue

            # Write to BigQuery pdf_index
            if not args.dry_run:
                bq_writer.log_pdf_status(
                    symbol=stock_id,
                    year=year,
                    report_type=period,
                    gcs_path=gcs_uri,
                    file_size_kb=file_size_kb,
                    download_status="downloaded",
                    title=pdf_path.stem,
                )

            print("✓" if not args.dry_run else "(dry-run)")
            success.append(f"{stock_name} {year}{period}")

    # Summary
    print(f"\n{'='*60}")
    action = "Would upload" if args.dry_run else "Uploaded"
    print(f"{action}: {len(success)}  |  Skipped: {len(skipped)}  |  Failed: {len(failed)}")
    if failed:
        print("\nFailed files:")
        for f in failed:
            print(f"  {f}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
