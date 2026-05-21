#!/usr/bin/env python3
"""extract_mcu_segments.py — Extract MCU segment revenue from annual report PDFs.

Pipeline:
  GCS PDF → pdfplumber text → DeepSeek V3 → mcu_segments (BQ) + mcu_known_data.json

Only processes companies where MCU revenue is NOT auto-derivable:
  segment_reported  : 兆易创新, 普冉股份
  segment_estimated : 复旦微电子, 乐鑫科技
  subsidiary_geehy  : 纳思达 (best-effort)
  estimated         : 国民技术, 芯海科技 (best-effort)

Usage:
    python extract_mcu_segments.py                  # all eligible PDFs in GCS
    python extract_mcu_segments.py 603986           # single company
    python extract_mcu_segments.py 603986 2023      # single company + year
    python extract_mcu_segments.py --local /path    # read from local dir instead of GCS

Environment:
    GCP_PROJECT         default: st-china-ai-force
    GCS_BUCKET          default: st-finance-reports
    DEEPSEEK_API_KEY    or fetched from Secret Manager
    GEMINI_API_KEY      fallback model
"""

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HERE = Path(__file__).parent

PROJECT = (os.environ.get("GCP_PROJECT")
           or os.environ.get("GOOGLE_CLOUD_PROJECT")
           or "st-china-ai-force")
BUCKET  = os.environ.get("GCS_BUCKET", "st-finance-reports")

# Companies where PDF extraction adds value (strategy ≠ total_revenue / total_proxy)
EXTRACT_TARGETS = {
    "603986": "segment_reported",    # 兆易创新  — 分产品表有MCU
    "688766": "segment_reported",    # 普冉股份  — 分产品表有MCU
    "688385": "segment_estimated",   # 复旦微电  — 智能计量芯片分段
    "688018": "segment_estimated",   # 乐鑫科技  — 芯片 vs 模组
    "002180": "subsidiary_geehy",    # 纳思达    — 极海子公司MCU
    "300077": "estimated",           # 国民技术  — MCU vs 安全芯片
    "688595": "estimated",           # 芯海科技  — MCU+模拟混合
}

_fx_path = Path(__file__).parent / "fx_rates.json"
FX: dict[int, float] = (
    {int(k): v for k, v in json.loads(_fx_path.read_text())["CNY_USD"].items()}
    if _fx_path.exists()
    else {2018:6.6174,2019:6.8985,2020:6.8976,2021:6.4515,
          2022:6.7261,2023:7.0809,2024:7.1900,2025:7.2200,2026:7.2500}
)


# ── Secret Manager ────────────────────────────────────────────────────────────

def get_secret(name: str, *aliases: str) -> str | None:
    """Fetch secret from env var first, then Secret Manager.

    Checks env vars derived from `name` and any extra `aliases` before
    falling back to Secret Manager (tries `name` then each alias as secret id).
    """
    # env var candidates: derived from name + explicit aliases
    env_candidates = [name.upper().replace("-", "_")] + list(aliases)
    for env_key in env_candidates:
        if v := os.environ.get(env_key):
            return v
    # Secret Manager candidates: name + aliases as secret ids
    secret_ids = [name] + list(aliases)
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        for sid in secret_ids:
            try:
                path = f"projects/{PROJECT}/secrets/{sid}/versions/latest"
                return client.access_secret_version(name=path).payload.data.decode()
            except Exception:
                continue
    except Exception as exc:
        log.debug("Secret Manager unavailable: %s", exc)
    return None


# ── GCS helpers ───────────────────────────────────────────────────────────────

def list_gcs_pdfs(symbol: str | None = None, year: int | None = None) -> list[dict]:
    """Return list of {symbol, year, report_type, gcs_path} for eligible PDFs."""
    try:
        from google.cloud import storage
    except ImportError:
        sys.exit("pip install google-cloud-storage")

    gcs    = storage.Client(project=PROJECT)
    bucket = gcs.bucket(BUCKET)
    prefix = "reports/"
    blobs  = list(bucket.list_blobs(prefix=prefix))

    results = []
    for blob in blobs:
        if not blob.name.endswith((".pdf", ".PDF")):
            continue
        parts = blob.name.split("/")   # reports/{sym}_{name}/{year}_{type}_*.pdf
        if len(parts) < 3:
            continue

        folder   = parts[1]           # e.g. "603986_兆易创新"
        filename = parts[2]
        sym_match = re.match(r"^(\d{6})", folder)
        if not sym_match:
            continue
        sym = sym_match.group(1)

        if sym not in EXTRACT_TARGETS:
            continue
        if symbol and sym != symbol:
            continue

        yr_match = re.match(r"^(20\d{2})", filename)
        if not yr_match:
            continue
        yr = int(yr_match.group(1))
        if year and yr != year:
            continue

        period = "年报"
        for p in ["一季报","半年报","三季报"]:
            if p in filename:
                period = p
                break

        # Skip quarterly reports for MCU extraction (annual reports have segment tables)
        if period != "年报":
            continue

        results.append({
            "symbol":      sym,
            "year":        yr,
            "report_type": period,
            "gcs_path":    f"gs://{BUCKET}/{blob.name}",
            "blob_name":   blob.name,
        })

    return sorted(results, key=lambda x: (x["symbol"], x["year"]))


def download_pdf(blob_name: str, dest_path: str) -> bool:
    try:
        from google.cloud import storage
        gcs    = storage.Client(project=PROJECT)
        bucket = gcs.bucket(BUCKET)
        blob   = bucket.blob(blob_name)
        blob.download_to_filename(dest_path, timeout=120)
        return True
    except Exception as exc:
        log.warning("GCS download failed %s: %s", blob_name, exc)
        return False


# ── PDF text extraction ───────────────────────────────────────────────────────

SEGMENT_KEYWORDS = [
    "分产品", "按产品", "主要产品", "产品构成",
    "MCU", "微控制器", "单片机",
    "营业收入", "主营业务收入",
    "智能计量", "电表", "芯片收入", "模组收入",
]

def _page_content(page) -> str:
    """Extract text + tables from one pdfplumber page.

    Tables are formatted as TSV so column alignment survives LLM tokenisation.
    """
    parts: list[str] = []

    # Plain text (headings, surrounding prose)
    text = page.extract_text() or ""
    if text.strip():
        parts.append(text)

    # Tables — TSV format preserves cell alignment far better than plain text
    try:
        tables = page.extract_tables() or []
    except Exception:
        tables = []
    for tbl in tables:
        rows = []
        for row in tbl:
            cells = [str(c or "").strip().replace("\n", " ") for c in row]
            rows.append("\t".join(cells))
        if rows:
            parts.append("【表格】\n" + "\n".join(rows))

    return "\n".join(parts)


def extract_relevant_pages(pdf_path: str, max_pages: int = 30) -> str:
    """Extract text+tables from PDF, prioritising pages with segment revenue tables."""
    try:
        import pdfplumber
    except ImportError:
        sys.exit("pip install pdfplumber")

    scored: list[tuple[int, int, str]] = []

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        log.debug("  PDF pages: %d", total)

        for i, page in enumerate(pdf.pages):
            try:
                content = _page_content(page)
            except Exception:
                continue
            score = sum(1 for kw in SEGMENT_KEYWORDS if kw in content)
            if score > 0:
                scored.append((score, i + 1, content))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max_pages]
    top.sort(key=lambda x: x[1])

    combined = "\n\n".join(f"[第{pg}页]\n{txt}" for _, pg, txt in top)
    log.info("  Extracted %d relevant pages (of %d total)", len(top), total)
    return combined


# ── LLM extraction ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一位专业的半导体行业财务分析师。
任务：从A股上市公司年报文本中，精确提取MCU（微控制器）产品的营业收入数据。

输出要求（严格JSON格式）：
{
  "mcu_revenue_yuan": <float或null>,       // MCU产品营业收入，单位：元。如无则null
  "mcu_revenue_note": "<string>",          // 数据来源描述，如"分产品营业收入表第X页"
  "other_segments": {                      // 其他主要产品线营收（元），可选
    "<产品名>": <float>
  },
  "gross_margin_pct": <float或null>,       // MCU产品毛利率%，如有
  "confidence": "high|medium|low",        // 提取置信度
  "reasoning": "<string>",               // 简要说明提取依据
  "source_text": "<string>"              // 原文关键片段（≤200字）
}

注意：
- 营收单位通常是"元"，有时是"万元"或"亿元"，请统一转换为元
- 【表格】标记的内容是用制表符分隔的表格，列顺序通常为：分产品/分行业 | 营业收入 | 营业成本 | 毛利率(%) | 收入同比(%) | 成本同比(%) | 毛利率变动
- 兆易创新：在"主营业务分产品情况"表中找"微控制器"行，取第2列（营业收入）
- 普冉股份：在"主营业务分产品情况"表中找"微控制器"行（区别于NOR Flash、SRAM）
- 复旦微电子：找"智能计量"或"MCU"相关芯片收入行
- 乐鑫科技：找"芯片"收入行（区别于"模组"收入）
- 纳思达/极海：找"集成电路"分部或极海微电子相关行
- 表格中数字含逗号（千分位）为正常格式，如"1,316,813,511.75"即1316813511.75元
- 如果找不到MCU分段数据，返回mcu_revenue_yuan为null并说明原因"""


def call_deepseek(text: str, company: str, year: int, api_key: str) -> dict | None:
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("pip install openai")

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )
    user_msg = f"""公司：{company}
财年：{year}年

以下是年报相关页面的文字内容：

{text[:12000]}

请提取MCU产品营业收入数据，输出JSON格式。"""

    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=1000,
        )
        raw = resp.choices[0].message.content
        return json.loads(raw)
    except Exception as exc:
        log.warning("DeepSeek call failed: %s", exc)
        return None


def call_gemini(text: str, company: str, year: int, api_key: str) -> dict | None:
    try:
        import google.generativeai as genai
    except ImportError:
        log.warning("pip install google-generativeai for Gemini fallback")
        return None

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    prompt = f"""{SYSTEM_PROMPT}

公司：{company}  财年：{year}年

年报内容：
{text[:12000]}

输出JSON："""

    try:
        resp = model.generate_content(prompt)
        raw  = resp.text.strip()
        # Strip markdown code fences if present
        raw  = re.sub(r"^```json\s*|\s*```$", "", raw, flags=re.MULTILINE)
        return json.loads(raw)
    except Exception as exc:
        log.warning("Gemini call failed: %s", exc)
        return None


def extract_with_llm(text: str, symbol: str, company_name: str,
                     year: int, deepseek_key: str | None,
                     gemini_key: str | None) -> dict | None:
    if deepseek_key:
        result = call_deepseek(text, company_name, year, deepseek_key)
        if result:
            result["_model"] = "deepseek-chat"
            return result

    if gemini_key:
        log.info("  Falling back to Gemini…")
        result = call_gemini(text, company_name, year, gemini_key)
        if result:
            result["_model"] = "gemini-2.0-flash"
            return result

    return None


# ── Result persistence ────────────────────────────────────────────────────────

def update_mcu_known_data(symbol: str, year: int, result: dict) -> None:
    """Write extracted result back to mcu_known_data.json."""
    path = HERE / "mcu_known_data.json"
    data = json.loads(path.read_text())

    mcu_yuan = result.get("mcu_revenue_yuan")
    if mcu_yuan is None:
        log.info("  No MCU revenue found — skipping mcu_known_data update")
        return

    fx  = FX.get(year, 7.2)
    musd = round(mcu_yuan / fx / 1_000_000, 2)

    conf_map = {"high": "high", "medium": "medium", "low": "low"}
    entry = {
        "mcu_revenue_yuan": mcu_yuan,
        "data_type":        "reported" if result.get("confidence") == "high" else "estimated",
        "confidence":       conf_map.get(result.get("confidence", "medium"), "medium"),
        "source":           result.get("mcu_revenue_note", f"LLM提取 {result.get('_model','')}"),
    }
    if result.get("mcu_gross_margin") is not None:
        entry["mcu_gross_margin"] = result["mcu_gross_margin"]

    data.setdefault(symbol, {})[str(year)] = entry
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    log.info("  Updated mcu_known_data.json: %s %d → %.1f M$ (¥%.0f)",
             symbol, year, musd, mcu_yuan)


def save_to_bq(symbol: str, year: int, result: dict,
               company_name: str) -> None:
    import bq_writer
    if not bq_writer.is_available():
        return

    mcu_yuan = result.get("mcu_revenue_yuan")
    bq_writer.write_mcu_segment(
        symbol=symbol,
        year=year,
        period="年报",
        mcu_revenue_yuan=mcu_yuan,
        source_type="annual_report",
        source_page=result.get("mcu_revenue_note", ""),
        raw_excerpt=result.get("source_text", "")[:2000],
        confidence={"high":1.0,"medium":0.7,"low":0.4}.get(
            result.get("confidence","medium"), 0.7),
        model=result.get("_model",""),
    )
    # Also patch the financials table
    if mcu_yuan is not None:
        fx   = FX.get(year, 7.2)
        musd = round(mcu_yuan / fx / 1_000_000, 2)
        bq_writer.write_financials(
            symbol=symbol,
            year=year,
            fin_row={
                "mcu_revenue_yuan": mcu_yuan,
                "mcu_revenue_musd": musd,
                "mcu_data_type":    "reported" if result.get("confidence") == "high" else "estimated",
                "mcu_confidence":   result.get("confidence","medium"),
                "mcu_source":       result.get("mcu_revenue_note",""),
                "pdf_extracted_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc).isoformat(),
            },
            meta={"name_cn": company_name, "mcu_strategy": EXTRACT_TARGETS.get(symbol,"")},
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def process_one(pdf_path: str, symbol: str, year: int,
                company_name: str, report_type: str,
                deepseek_key: str | None, gemini_key: str | None,
                update_json: bool = True) -> bool:
    log.info("Extracting: %s %s %d…", company_name, report_type, year)

    text = extract_relevant_pages(pdf_path)
    if not text.strip():
        log.warning("  No text extracted from PDF — skipping")
        return False

    result = extract_with_llm(text, symbol, company_name, year,
                               deepseek_key, gemini_key)
    if result is None:
        log.warning("  LLM extraction failed — no API key available?")
        return False

    mcu_yuan = result.get("mcu_revenue_yuan")
    conf     = result.get("confidence", "?")
    model    = result.get("_model", "?")
    note     = result.get("mcu_revenue_note", "")

    if mcu_yuan:
        fx   = FX.get(year, 7.2)
        musd = mcu_yuan / fx / 1_000_000
        log.info("  ✓ MCU revenue: ¥%.0f万 (%.1f M$)  conf=%s  [%s]",
                 mcu_yuan / 10000, musd, conf, model)
    else:
        log.info("  ✗ MCU revenue not found  conf=%s  reason: %s",
                 conf, result.get("reasoning",""))

    save_to_bq(symbol, year, result, company_name)
    if update_json:
        update_mcu_known_data(symbol, year, result)

    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract MCU segment revenue from annual report PDFs")
    parser.add_argument("symbol", nargs="?", help="Stock code filter, e.g. 603986")
    parser.add_argument("year",   nargs="?", type=int, help="Year filter, e.g. 2023")
    parser.add_argument("--local", metavar="DIR",
                        help="Read PDFs from local directory instead of GCS")
    parser.add_argument("--no-update-json", action="store_true",
                        help="Skip updating mcu_known_data.json")
    args = parser.parse_args()

    deepseek_key = get_secret("deepseek-api-key", "VITE_DEEPSEEK_API_KEY")
    gemini_key   = get_secret("gemini-api-key",   "VITE_GEMINI_API_KEY")

    if not deepseek_key and not gemini_key:
        sys.exit(
            "No API key found.\n"
            "Set DEEPSEEK_API_KEY (or VITE_DEEPSEEK_API_KEY) env var, or Secret Manager:\n"
            "  export VITE_DEEPSEEK_API_KEY=sk-...\n"
            "  export GEMINI_API_KEY=AIza..."
        )

    log.info("API: %s%s",
             "DeepSeek " if deepseek_key else "",
             "Gemini"    if gemini_key   else "")

    # Load company names
    meta_path = HERE / "companies_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    if args.local:
        # Local mode: scan local directory
        local_dir = Path(args.local)
        pdfs = []
        for folder in sorted(local_dir.iterdir()):
            if not folder.is_dir():
                continue
            sym_m = re.match(r"^(\d{6})", folder.name)
            if not sym_m or sym_m.group(1) not in EXTRACT_TARGETS:
                continue
            sym = sym_m.group(1)
            if args.symbol and sym != args.symbol:
                continue
            for pdf in sorted(folder.glob("*.pdf")) + sorted(folder.glob("*.PDF")):
                yr_m = re.match(r"^(20\d{2})", pdf.name)
                if not yr_m:
                    continue
                yr = int(yr_m.group(1))
                if args.year and yr != args.year:
                    continue
                if "一季报" in pdf.name or "半年报" in pdf.name or "三季报" in pdf.name:
                    continue
                pdfs.append({
                    "symbol": sym, "year": yr, "report_type": "年报",
                    "local_path": str(pdf),
                })
        source = "local"
    else:
        # GCS mode
        pdfs = list_gcs_pdfs(args.symbol, args.year)
        source = "GCS"

    if not pdfs:
        log.info("No eligible PDFs found in %s", source)
        return

    log.info("Found %d PDFs to process from %s", len(pdfs), source)

    ok, skip, fail = 0, 0, 0

    for item in pdfs:
        sym  = item["symbol"]
        yr   = item["year"]
        name = meta.get(sym, {}).get("name_cn", sym)

        # Skip if already in mcu_known_data.json with high confidence
        known = json.loads((HERE / "mcu_known_data.json").read_text())
        existing = known.get(sym, {}).get(str(yr))
        if (existing and isinstance(existing, dict)
                and existing.get("confidence") == "high"
                and existing.get("data_type") == "reported"):
            log.info("Skipping %s %d — already in mcu_known_data (high/reported)", name, yr)
            skip += 1
            continue

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            if source == "local":
                tmp_path = item["local_path"]
                need_cleanup = False
            else:
                log.info("Downloading %s…", item["gcs_path"])
                ok_dl = download_pdf(item["blob_name"], tmp_path)
                need_cleanup = True
                if not ok_dl:
                    fail += 1
                    continue

            success = process_one(
                pdf_path=tmp_path,
                symbol=sym,
                year=yr,
                company_name=name,
                report_type=item["report_type"],
                deepseek_key=deepseek_key,
                gemini_key=gemini_key,
                update_json=not args.no_update_json,
            )
            if success:
                ok += 1
            else:
                fail += 1
        finally:
            if need_cleanup:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    log.info("Done — extracted: %d  skipped: %d  failed: %d", ok, skip, fail)


if __name__ == "__main__":
    main()
