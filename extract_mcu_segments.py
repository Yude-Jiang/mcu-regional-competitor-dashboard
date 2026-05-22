#!/usr/bin/env python3
"""extract_mcu_segments.py — Extract MCU segment revenue from annual report PDFs.

Pipeline A (default): Gemini Files API — native PDF, no pdfplumber:
  GCS PDF → Gemini Files API → mcu_known_data.json + BQ

Pipeline B (--model deepseek): pdfplumber text → DeepSeek V3:
  GCS PDF → pdfplumber text+tables → DeepSeek V3 → mcu_known_data.json + BQ

  Advantages of Gemini native PDF:
  - Reads visual layout directly — no column-alignment garbling from pdfplumber
  - 1M token context = full annual report in one call (no page scoring needed)
  - Handles scanned/image PDFs via built-in OCR
  - Recommended for 分产品表 extraction (tables with merged cells, rotated text)

Only processes companies where MCU revenue is NOT auto-derivable:
  segment_reported  : 兆易创新, 普冉股份
  segment_estimated : 复旦微电子, 乐鑫科技
  subsidiary_geehy  : 纳思达 (best-effort)
  estimated         : 国民技术, 芯海科技 (best-effort)

Usage:
    python extract_mcu_segments.py                       # all eligible PDFs in GCS
    python extract_mcu_segments.py 603986                # single company
    python extract_mcu_segments.py 603986 2023           # single company + year
    python extract_mcu_segments.py --model gemini        # use Gemini native PDF
    python extract_mcu_segments.py --local /path         # read from local dir

Environment:
    GCP_PROJECT         default: st-china-ai-force
    GCS_BUCKET          default: st-finance-reports
    VITE_DEEPSEEK_API_KEY  or fetched from Secret Manager
    VITE_GEMINI_API_KEY    required for --model gemini
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
    "300327": "segment_industrial",  # 中颖电子  — 工业控制芯片分产品表
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
            log.debug("Key '%s' from env var %s (prefix: %s…)", name, env_key, v[:8])
            return v
    # Secret Manager candidates: name + aliases as secret ids
    secret_ids = [name] + list(aliases)
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        for sid in secret_ids:
            try:
                path = f"projects/{PROJECT}/secrets/{sid}/versions/latest"
                v = client.access_secret_version(name=path).payload.data.decode()
                log.debug("Key '%s' from Secret Manager secret '%s' (prefix: %s…)",
                          name, sid, v[:8])
                return v
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
        is_prospectus = any(k in filename for k in ["招募书", "招股书", "prospectus", "IPO"])
        if not yr_match and not is_prospectus:
            continue
        if is_prospectus:
            yr = 0  # year unknown for prospectus
            period = "招募书"
            if year:  # --year filter skips prospectus
                continue
        else:
            yr = int(yr_match.group(1))
            if year and yr != year:
                continue
            period = "年报"
            for p in ["一季报","半年报","三季报"]:
                if p in filename:
                    period = p
                    break
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
    """Download a PDF from GCS. Tries gcloud storage cp → gsutil cp → Python SDK."""
    import subprocess
    gcs_uri = f"gs://{BUCKET}/{blob_name}"

    for cmd in (["gcloud", "storage", "cp", gcs_uri, dest_path],
                ["gsutil", "cp", gcs_uri, dest_path]):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode == 0:
                return True
            log.warning("%s failed (%d): %s", cmd[0], result.returncode,
                        result.stderr.strip()[:200])
        except FileNotFoundError:
            continue  # CLI not available
        except subprocess.TimeoutExpired:
            log.warning("%s timed out after 600s", cmd[0])
        except Exception as exc:
            log.warning("%s error: %s", cmd[0], exc)

    # Final fallback: Python GCS SDK (no timeout — let it run as long as needed)
    try:
        from google.cloud import storage
        gcs    = storage.Client(project=PROJECT)
        bucket = gcs.bucket(BUCKET)
        blob   = bucket.blob(blob_name)
        blob.download_to_filename(dest_path, timeout=None)
        return True
    except Exception as exc:
        log.warning("GCS SDK download failed %s: %s", blob_name, exc)
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
- 兆易创新：在"主营业务分产品情况"表中找"微控制器"或"MCU及模拟产品"行（两种命名均指同一产品线），取第2列（营业收入）
- 中颖电子（300327）：在"主营业务分产品情况"表中找"工业控制芯片"行（含BMIC电池管理芯片，非纯MCU但为官方口径），取营业收入列
- 普冉股份：在"主营业务分产品情况"表中找"微控制器"行（区别于NOR Flash、SRAM）
- 复旦微电子：找"智能计量"或"MCU"相关芯片收入行
- 乐鑫科技：找"芯片"收入行（区别于"模组"收入）
- 纳思达/极海（002180）：年报不单独披露MCU，请用「芯片」产品营收作为mcu_revenue_yuan（区别于「模组」收入）；集成电路产业总营收≈14亿，其中「芯片」≈8亿即极海MCU口径，置信度medium
- 国民技术（300077）：年报未拆分MCU vs安全芯片；如有分产品表，查找「MCU」或「微控制器」行；若仅有合并披露则返回null
- 芯海科技（688595）：在「主营业务分产品情况」或管理层讨论章节找「MCU和AIoT芯片」行（注意：部分年报将此合并为「测量与控制芯片」或「MCU芯片」，取含MCU的最大口径行）；不要取「模拟信号链芯片」行；若完全无法拆分则返回null
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
    """Gemini text-based fallback (same pipeline as DeepSeek but different model)."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        log.warning("pip install google-genai")
        return None

    client = genai.Client(api_key=api_key)
    prompt = f"""{SYSTEM_PROMPT}

公司：{company}  财年：{year}年

年报内容：
{text[:12000]}

输出JSON："""

    try:
        resp = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=1000,
                response_mime_type="application/json",
            ),
        )
        raw = resp.text.strip()
        try:
            return _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as parse_exc:
            log.warning("Gemini text call — JSON parse failed: %s", parse_exc)
            log.debug("  Raw response (first 500 chars): %s", raw[:500])
            return None
    except Exception as exc:
        log.warning("Gemini text call failed: %s", exc)
        return None


def _extract_json(raw: str) -> dict:
    """Robustly extract a JSON object from LLM response text.

    Handles: bare JSON, ```json fences, prose before/after the object,
    and extra content after the closing brace (uses raw_decode).
    Raises ValueError if no valid JSON object found.
    """
    raw = raw.strip()
    decoder = json.JSONDecoder()

    # 1. Direct parse (handles clean JSON)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 2. Strip ``` fences (multiline, with or without 'json' tag)
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    # 3. raw_decode from first '{' — reads exactly one JSON object,
    #    ignores any trailing content (newlines, extra text, second objects)
    brace_start = raw.find("{")
    if brace_start != -1:
        try:
            obj, _ = decoder.raw_decode(raw, brace_start)
            return obj
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No JSON object found in response (first 300 chars): {raw[:300]}")


def call_gemini_stream_from_gcs(gcs_uri: str, company: str, year: int,
                                 api_key: str) -> dict | None:
    """GCS → BytesIO → inline bytes in generate_content (no Files API upload).

    Sends PDF bytes inline in the request body (max ~20MB).
    Avoids Files API entirely — works with API keys that restrict upload endpoint.
    """
    try:
        import io
        from google import genai
        from google.genai import types
        from google.cloud import storage
    except ImportError:
        return None

    if not gcs_uri.startswith("gs://"):
        return None
    path = gcs_uri[5:]
    bucket_name, _, blob_name = path.partition("/")

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"公司：{company}  财年：{year}年\n\n"
        "请从上传的年报PDF中提取MCU产品营业收入数据，输出JSON格式。"
    )
    try:
        log.info("  Streaming PDF: GCS → BytesIO (inline bytes)…")
        gcs_client = storage.Client(project=PROJECT)
        buf = io.BytesIO()
        gcs_client.bucket(bucket_name).blob(blob_name).download_to_file(
            buf, timeout=None
        )
        buf.seek(0)
        pdf_bytes = buf.read()
        log.info("  GCS download complete (%.1f MB), sending inline…",
                 len(pdf_bytes) / 1e6)

        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                prompt,
            ],
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=2000,
                response_mime_type="application/json",
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True,
                ),
            ),
        )
        raw = resp.text.strip()
        try:
            result = _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as parse_exc:
            log.warning("  JSON parse failed: %s", parse_exc)
            log.debug("  Raw response (first 500 chars): %s", raw[:500])
            return None
        result["_model"] = "gemini-3.5-flash-inline"
        log.info("  Inline PDF extraction complete")
        return result

    except Exception as exc:
        log.warning("  GCS stream failed: %s", exc)
        return None


def call_gemini_gcs_uri(gcs_uri: str, company: str, year: int) -> dict | None:
    """Vertex AI Gemini — read PDF directly from GCS URI via ADC (v1 endpoint).

    Fallback for projects where Vertex AI Generative AI is enabled.
    """
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"公司：{company}  财年：{year}年\n\n"
        "请从上传的年报PDF中提取MCU产品营业收入数据，输出JSON格式。"
    )
    _VERTEX_MODELS = [
        "gemini-3.5-flash",          # GA 2026-05-20, 推荐首选
        "gemini-3.1-pro-preview",    # SOTA 推理，长文档
        "gemini-3-flash-preview",    # GA 2025-12-18
        "gemini-3.1-flash-lite",     # 高性价比
        "gemini-2.0-flash-001",      # 旧版兜底
        "gemini-1.5-flash-002",
    ]
    _LOCATIONS = ["us-central1", "us-east4", "asia-east1", "asia-northeast1"]

    # Try vertexai SDK first (v1 endpoint, more widely enabled)
    try:
        import warnings
        import vertexai
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from vertexai.generative_models import GenerativeModel, Part as VPart
        for loc in _LOCATIONS:
            for model_id in _VERTEX_MODELS:
                try:
                    vertexai.init(project=PROJECT, location=loc)
                    model = GenerativeModel(model_id)
                    resp = model.generate_content([
                        VPart.from_uri(gcs_uri, mime_type="application/pdf"),
                        prompt,
                    ])
                    result = _extract_json(resp.text.strip())
                    result["_model"] = f"{model_id}-vertex-gcs"
                    log.info("  Vertex AI GCS extraction complete (%s @ %s)", model_id, loc)
                    return result
                except Exception as exc:
                    err = str(exc)
                    if "NOT_FOUND" in err or "404" in err:
                        continue   # try next model
                    log.warning("  vertexai %s@%s: %s", model_id, loc, err[:120])
                    break          # non-404 error — skip to next location
    except ImportError:
        log.warning("pip install google-cloud-aiplatform for Vertex AI path")

    log.warning("  All Vertex AI model/location combos failed")
    return None


def call_gemini_native_pdf(pdf_path: str, company: str, year: int,
                            api_key: str) -> dict | None:
    """Gemini Files API — send PDF directly without pdfplumber text extraction.

    This is the preferred path for tables with complex layout (分产品营收表).
    The model sees the visual rendering of the PDF, so column alignment is exact.
    Supports up to 1M tokens = entire annual report in one call.
    Also handles scanned/image-only PDFs via built-in OCR.
    """
    try:
        from google import genai
    except ImportError:
        log.warning("pip install google-genai")
        return None

    client = genai.Client(api_key=api_key)
    uploaded = None

    try:
        log.info("  Uploading PDF to Gemini Files API…")
        uploaded = client.files.upload(
            file=pdf_path,
            config={"mime_type": "application/pdf"},
        )

        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"公司：{company}  财年：{year}年\n\n"
            "请从上传的年报PDF中提取MCU产品营业收入数据，输出JSON格式。"
        )
        resp = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=[uploaded, prompt],
        )
        raw = resp.text.strip()
        try:
            result = _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as parse_exc:
            log.warning("  Gemini native PDF — JSON parse failed: %s", parse_exc)
            log.debug("  Raw response (first 500 chars): %s", raw[:500])
            return None
        result["_model"] = "gemini-2.0-flash-native-pdf"
        log.info("  Gemini native PDF extraction complete")
        return result

    except Exception as exc:
        log.warning("Gemini native PDF failed: %s", exc)
        return None

    finally:
        if uploaded:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


def extract_with_llm(text: str, symbol: str, company_name: str,
                     year: int, pdf_path: str,
                     deepseek_key: str | None, gemini_key: str | None,
                     model_pref: str = "deepseek",
                     gcs_uri: str | None = None) -> dict | None:
    """Run LLM extraction. model_pref: 'deepseek' | 'gemini' | 'gemini-native'"""

    if model_pref in ("gemini", "gemini-native"):
        # Path 1: GCS → BytesIO → Gemini Files API (Developer API key, 全程Google内网)
        if gcs_uri and gemini_key:
            result = call_gemini_stream_from_gcs(gcs_uri, company_name, year, gemini_key)
            if result:
                return result
            log.info("  GCS stream failed, trying Vertex AI GCS URI…")

        # Path 2: Vertex AI GCS URI (ADC, requires Vertex AI API enabled)
        if gcs_uri:
            result = call_gemini_gcs_uri(gcs_uri, company_name, year)
            if result:
                return result
            log.info("  Vertex AI GCS path failed, trying local Files API upload…")

        # Path 3: Files API upload (local PDF already on disk)
        if gemini_key and pdf_path and Path(pdf_path).exists():
            result = call_gemini_native_pdf(pdf_path, company_name, year, gemini_key)
            if result:
                return result
            log.info("  Gemini native PDF failed, falling back to text path…")
        elif not (pdf_path and Path(pdf_path).exists()):
            log.info("  No local PDF for Files API (gemini-native skips disk download)")

    if model_pref == "gemini" and gemini_key and text:
        result = call_gemini(text, company_name, year, gemini_key)
        if result:
            result["_model"] = "gemini-2.0-flash"
            return result

    if deepseek_key and text:
        result = call_deepseek(text, company_name, year, deepseek_key)
        if result:
            result["_model"] = "deepseek-chat"
            return result

    if gemini_key and text and model_pref == "deepseek":
        log.info("  DeepSeek failed, falling back to Gemini text…")
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
                update_json: bool = True, model_pref: str = "deepseek",
                gcs_uri: str | None = None) -> bool:
    log.info("Extracting: %s %s %d  [model=%s]…", company_name, report_type, year, model_pref)

    # Gemini native PDF skips pdfplumber entirely
    if model_pref in ("gemini", "gemini-native"):
        text = ""   # not needed; pdf_path or gcs_uri is used directly
    else:
        text = extract_relevant_pages(pdf_path)
        if not text.strip():
            log.warning("  No text extracted from PDF — skipping")
            return False

    result = extract_with_llm(text, symbol, company_name, year,
                               pdf_path=pdf_path,
                               deepseek_key=deepseek_key,
                               gemini_key=gemini_key,
                               model_pref=model_pref,
                               gcs_uri=gcs_uri)
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
    parser.add_argument("--debug", action="store_true",
                        help="Set log level to DEBUG (shows raw LLM responses on parse failure)")
    parser.add_argument("--model", choices=["deepseek", "gemini", "gemini-native"],
                        default="gemini-native",
                        help=(
                            "gemini-native: Gemini Files API直接读PDF，保留视觉布局（默认）  "
                            "gemini: pdfplumber→Gemini text  "
                            "deepseek: pdfplumber→DeepSeek"
                        ))
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

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    _llm_model = "gemini-3.5-flash" if args.model in ("gemini", "gemini-native") else "deepseek-chat"
    log.info("API: %s%s  pipeline=%s  llm=%s",
             "DeepSeek " if deepseek_key else "",
             "Gemini"    if gemini_key   else "",
             args.model, _llm_model)

    # Load company names
    meta_path = HERE / "companies_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    if args.local:
        local_path = Path(args.local)
        pdfs = []

        if local_path.is_file():
            # Single-file mode: --local /path/to/file.pdf --symbol XXXXXX [--year YYYY]
            if not args.symbol:
                sys.exit("--local <file> requires --symbol XXXXXX")
            yr = args.year or 0  # 0 = unknown year, pipeline will still run
            pdfs.append({
                "symbol": args.symbol, "year": yr, "report_type": "招募书",
                "local_path": str(local_path),
            })
        else:
            # Directory mode: scan subdirs named XXXXXX_*
            for folder in sorted(local_path.iterdir()):
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
            gcs_uri      = None
            need_cleanup = False

            if source == "local":
                tmp_path = item["local_path"]
            else:
                gcs_uri = f"gs://{BUCKET}/{item['blob_name']}"
                # gemini-native: extract_with_llm will stream GCS→Gemini internally
                # (Path 1: GCS stream; Path 2: Vertex AI; Path 3: local Files API)
                # Only download to disk if text-based models (deepseek/gemini-text) are needed
                if args.model not in ("gemini", "gemini-native"):
                    log.info("Downloading %s…", item["gcs_path"])
                    ok_dl = download_pdf(item["blob_name"], tmp_path)
                    need_cleanup = True
                    if not ok_dl:
                        fail += 1
                        continue

            success = process_one(
                pdf_path=tmp_path if need_cleanup else "",
                symbol=sym,
                year=yr,
                company_name=name,
                report_type=item["report_type"],
                deepseek_key=deepseek_key,
                gemini_key=gemini_key,
                update_json=not args.no_update_json,
                model_pref=args.model,
                gcs_uri=gcs_uri,
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
