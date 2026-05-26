#!/usr/bin/env python3
"""extract_employee_counts.py — 从GCS年报PDF中批量提取历史员工总数

使用 Gemini inline-bytes 模式（无需 Files API），直接读取年报PDF第一部分，
提取「报告期末在职员工总数」，写入 data.json 的 employee_count 字段。

用法（Colab）:
    # 全量跑（11家公司，2018-2024）
    python extract_employee_counts.py

    # 单公司
    python extract_employee_counts.py --symbol 603986

    # 单公司单年
    python extract_employee_counts.py --symbol 603986 --year 2023

    # Dry run（只打印不写入）
    python extract_employee_counts.py --dry-run

环境变量:
    VITE_GEMINI_API_KEY   Gemini API Key（必须）
    GCS_BUCKET            默认 st-finance-reports
    GCP_PROJECT           默认 st-china-ai-force
"""

import argparse
import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HERE      = Path(__file__).parent
DATA_JSON = HERE / "data.json"
EMP_JSON  = HERE / "employee_known_data.json"

PROJECT = os.environ.get("GCP_PROJECT", "st-china-ai-force")
BUCKET  = os.environ.get("GCS_BUCKET", "st-finance-reports")

# 11家目标公司
ALL_SYMBOLS = [
    "603986", "300327", "688380", "300077", "688279",
    "002180", "688385", "688766", "688595", "688391", "688018",
]

EMPLOYEE_PROMPT = """你是一名专业的财报数据提取助手。请从上传的A股年报PDF中精确提取以下数据：

**目标字段：报告期末在职员工总数**

查找位置（按优先级）：
1. 「董事会报告」→「员工情况」→「报告期末员工总数」
2. 「重要事项」→「员工及薪酬政策」
3. 年报首页摘要表格中的员工数
4. 「社会责任报告」章节中的员工数

注意：
- 只要整体公司层面的员工总数（在职，含全职），不要子公司或部门分项
- 单位是「人」，输出整数
- 如找到多个数字，取最大的在职总数
- 若年报中完全没有该数据，返回 null

请仅输出以下 JSON，不要任何其他文字：
{"employee_count": <整数或null>, "source_text": "<年报中找到的原文片段，不超过80字>"}

公司：{company}　财年：{year}年"""


def get_gemini_key() -> str:
    key = os.environ.get("VITE_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        # Try Secret Manager
        try:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{PROJECT}/secrets/VITE_GEMINI_API_KEY/versions/latest"
            resp = client.access_secret_version(request={"name": name})
            key = resp.payload.data.decode("utf-8").strip()
            log.info("Loaded Gemini key from Secret Manager")
        except Exception:
            pass
    if not key:
        sys.exit("ERROR: set VITE_GEMINI_API_KEY environment variable")
    return key


def list_annual_report_pdfs(symbol_filter=None, year_filter=None) -> list[dict]:
    """列出GCS中所有年报PDF（全部11家公司）。"""
    try:
        from google.cloud import storage
    except ImportError:
        sys.exit("pip install google-cloud-storage")

    gcs    = storage.Client(project=PROJECT)
    bucket = gcs.bucket(BUCKET)
    blobs  = list(bucket.list_blobs(prefix="reports/"))

    results = []
    for blob in blobs:
        if not blob.name.lower().endswith(".pdf"):
            continue
        parts = blob.name.split("/")
        if len(parts) < 3:
            continue

        folder   = parts[1]   # e.g. "603986_兆易创新"
        filename = parts[2]

        sym_match = re.match(r"^(\d{6})", folder)
        if not sym_match:
            continue
        sym = sym_match.group(1)

        if sym not in ALL_SYMBOLS:
            continue
        if symbol_filter and sym != symbol_filter:
            continue

        # 跳过招股书/半年报/季报
        is_annual = True
        for skip_kw in ["招募书", "招股书", "prospectus", "IPO", "半年报", "一季报", "三季报"]:
            if skip_kw in filename:
                is_annual = False
                break
        if not is_annual:
            continue

        yr_match = re.match(r"^(20\d{2})", filename)
        if not yr_match:
            continue
        yr = int(yr_match.group(1))

        if year_filter and yr != year_filter:
            continue

        results.append({
            "symbol":    sym,
            "year":      yr,
            "blob_name": blob.name,
            "gcs_uri":   f"gs://{BUCKET}/{blob.name}",
        })

    return sorted(results, key=lambda x: (x["symbol"], x["year"]))


def extract_employee_from_pdf(blob_name: str, company: str, year: int, api_key: str) -> dict | None:
    """下载PDF → Gemini inline bytes → 返回 {employee_count, source_text}。"""
    try:
        from google import genai
        from google.genai import types
        from google.cloud import storage
    except ImportError:
        sys.exit("pip install google-genai google-cloud-storage")

    # Download PDF
    try:
        gcs_client = storage.Client(project=PROJECT)
        buf = io.BytesIO()
        gcs_client.bucket(BUCKET).blob(blob_name).download_to_file(buf, timeout=None)
        buf.seek(0)
        pdf_bytes = buf.read()
        log.info("  Downloaded %.1f MB", len(pdf_bytes) / 1e6)
    except Exception as e:
        log.warning("  GCS download failed: %s", e)
        return None

    prompt = EMPLOYEE_PROMPT.format(company=company, year=year)

    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                prompt,
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=300,
                response_mime_type="application/json",
            ),
        )
        raw = resp.text.strip()
        log.debug("  Raw response: %s", raw[:200])

        # Parse JSON
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if not m:
            log.warning("  No JSON found in response: %s", raw[:100])
            return None
        result = json.loads(m.group())
        return result

    except Exception as e:
        log.warning("  Gemini call failed: %s", e)
        return None


def update_data_json(updates: dict, dry_run: bool = False) -> None:
    """将 {symbol: {year: employee_count}} 写入 data.json 和 employee_known_data.json。"""
    with open(DATA_JSON) as f:
        data = json.load(f)

    emp_known: dict = json.loads(EMP_JSON.read_text()) if EMP_JSON.exists() else {}

    changed = 0
    for sym, year_map in updates.items():
        if sym not in data["companies"]:
            log.warning("Symbol %s not in data.json", sym)
            continue
        for yr_str, count in year_map.items():
            fin = data["companies"][sym]["financials"].get(str(yr_str))
            if fin is None:
                log.warning("  %s year %s not in financials", sym, yr_str)
                continue
            old = fin.get("employee_count")
            if count is not None and old != count:
                if not dry_run:
                    fin["employee_count"] = count
                    # Persist to employee_known_data.json so fetch_mcu_data.py preserves it
                    emp_known.setdefault(sym, {})[str(yr_str)] = count
                log.info("  %s %s: employee_count %s → %s", sym, yr_str, old, count)
                changed += 1

    if dry_run:
        log.info("DRY RUN — %d changes would be written", changed)
        return

    if changed:
        with open(DATA_JSON, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        EMP_JSON.write_text(json.dumps(emp_known, ensure_ascii=False, indent=2))
        log.info("Wrote %d employee_count updates to data.json + employee_known_data.json", changed)
    else:
        log.info("No changes to write")


def main():
    parser = argparse.ArgumentParser(description="Extract employee counts from annual report PDFs")
    parser.add_argument("--symbol", help="Filter to single company (e.g. 603986)")
    parser.add_argument("--year",   type=int, help="Filter to single year (e.g. 2023)")
    parser.add_argument("--dry-run", action="store_true", help="Print results, do not write data.json")
    parser.add_argument("--skip-existing", action="store_true", default=True,
                        help="Skip (symbol, year) pairs that already have employee_count (default: True)")
    parser.add_argument("--force", action="store_true",
                        help="Re-extract even if employee_count already exists")
    args = parser.parse_args()

    api_key = get_gemini_key()

    # Load existing data to check what's already filled
    with open(DATA_JSON) as f:
        data = json.load(f)

    pdfs = list_annual_report_pdfs(
        symbol_filter=args.symbol,
        year_filter=args.year,
    )
    log.info("Found %d annual report PDFs", len(pdfs))

    updates: dict[str, dict[int, int | None]] = {}
    skipped = 0
    failed  = 0

    for item in pdfs:
        sym  = item["symbol"]
        year = item["year"]

        # Skip 2025 (already filled via XueQiu API)
        if year == 2025 and not args.force:
            skipped += 1
            continue

        # Skip if already filled
        if not args.force:
            existing = (data["companies"].get(sym, {})
                        .get("financials", {})
                        .get(str(year), {})
                        .get("employee_count"))
            if existing is not None:
                log.info("SKIP %s %d (already %d)", sym, year, existing)
                skipped += 1
                continue

        co_meta = data["companies"].get(sym, {}).get("meta", {})
        company = co_meta.get("name_cn", sym)

        log.info("Processing %s %s %d …", sym, company, year)

        result = extract_employee_from_pdf(item["blob_name"], company, year, api_key)

        if result is None:
            log.warning("  FAILED %s %d", sym, year)
            failed += 1
            continue

        count = result.get("employee_count")
        src   = result.get("source_text", "")
        log.info("  → employee_count=%s  src=「%s」", count, src[:60])

        if sym not in updates:
            updates[sym] = {}
        updates[sym][year] = count

        # Rate limit: 2s between calls to avoid Gemini quota
        time.sleep(2)

    log.info("=" * 60)
    log.info("Done. Extracted: %d  Skipped: %d  Failed: %d",
             len([v for ym in updates.values() for v in ym.values() if v is not None]),
             skipped, failed)

    if updates:
        update_data_json(updates, dry_run=args.dry_run)
        if not args.dry_run:
            print("\n✅ data.json + employee_known_data.json updated. Next steps:")
            print("   python validate_data.py")
            print("   git add data.json employee_known_data.json")
            print("   git commit -m 'feat(data): add historical employee counts from annual reports'")
            print("   git push")
    else:
        log.info("No updates to write.")


if __name__ == "__main__":
    main()
