#!/usr/bin/env python3
"""fetch_ir_records.py — 从巨潮资讯IR记录提取MCU营收数据

从 CNINFO 下载以下三类文件并用 LLM 提取 MCU 相关数字：
  1. 投资者关系活动记录 (调研记录、业绩说明会纪要)
  2. 上市公司公告搜索"MCU"/"微控制器"关键词

适用场景（年报未披露分产品MCU口径的公司）：
  002180 纳思达/极海微  — 极海子公司营收
  300077 国民技术       — MCU vs 安全芯片拆分 (CLAUDE.md: IR问答×0.27)
  688385 复旦微电子     — 智能电表芯片口径细化
  688018 乐鑫科技       — 芯片/模组拆分
  688595 芯海科技       — MCU vs 模拟比例

Usage (Cloud Shell):
  pip install pdfplumber openai -q
  python fetch_ir_records.py                      # all target companies, latest 3 years
  python fetch_ir_records.py --symbols 002180 300077
  python fetch_ir_records.py --symbols 002180 --year-start 2021
  python fetch_ir_records.py --dry-run            # list files only, no extraction

Output:
  直接写入 mcu_known_data.json（confidence=medium，data_type=ir_record）
  同时上传结构化结果到 BigQuery（如 BQ 可用）
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
from typing import Optional

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(message)s",
                    datefmt="%H:%M:%S")

# ── Config ────────────────────────────────────────────────────────────────────

HERE = Path(__file__).parent

CNINFO_COOKIE = os.environ.get("CNINFO_COOKIE", "")

# 目标公司：年报未直接披露MCU口径，需从IR记录补全
TARGET_SYMBOLS = ["002180", "300077", "688385", "688018", "688595"]

COMPANY_INFO = {
    "002180": {"name": "纳思达",   "name_short": "极海微/Geehy", "orgId": "9900003822", "market": "szse"},
    "300077": {"name": "国民技术", "name_short": "NationZ",      "orgId": "9900011747", "market": "szse"},
    "688385": {"name": "复旦微电", "name_short": "FDM",          "orgId": "gshk0008102","market": "sse"},
    "688018": {"name": "乐鑫科技", "name_short": "Espressif",    "orgId": "9900039017", "market": "sse"},
    "688595": {"name": "芯海科技", "name_short": "Chipsea",      "orgId": "gfbj0837517","market": "sse"},
    # 也可用于年报有数据的公司，做交叉验证
    "603986": {"name": "兆易创新", "name_short": "GigaDevice",   "orgId": "9900026561", "market": "sse"},
    "688766": {"name": "普冉股份", "name_short": "Puya",         "orgId": "nssc1000720","market": "sse"},
}

# CNINFO 公告类别
# 正确代码：巨潮资讯网 category 字段，可通过浏览器 DevTools → XHR 确认
CATEGORY_IR    = "category_iractivty_szsh"   # 投资者关系活动记录
CATEGORY_OTHER = "category_qita_szsh"        # 其他公告（含部分业绩说明会）
# 备用：直接用空 category 全量搜索，依赖 searchkey 过滤
CATEGORY_ALL   = ""

QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.cninfo.com.cn/",
    "Origin":  "https://www.cninfo.com.cn",
}

YEAR_START_DEFAULT = 2021
YEAR_END_DEFAULT   = 2025

MAX_DOCS_PER_COMPANY = 30   # 每家公司最多处理文件数，避免超时
MAX_PAGES_PER_DOC    = 20   # 每个PDF最多读取页数

# ── LLM System Prompt ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是半导体行业财务数据提取专家，专注于中国大陆MCU上市公司。
从以下投资者关系活动记录/业绩说明会纪要中提取MCU（微控制器）相关营收数据。

{company_hint}

提取规则：
1. 只提取明确数字，不推算，不猜测
2. 优先提取：MCU营收金额（万元/亿元/M$），MCU毛利率，MCU占总营收比例
3. 若文档提到"极海"/"Geehy"子公司，视为MCU口径
4. 若数字有"约""大概"等模糊词，标注 confidence=low
5. 对于002180纳思达：集团总收入264亿不是MCU，要找极海微子公司或MCU事业部单独数字
6. 对于300077国民技术：MCU vs 安全芯片的比例或绝对值均有价值
7. 若文档中无MCU具体数字，返回 found=false

严格按照以下JSON格式返回，不要其他文字：
{
  "found": true,
  "records": [
    {
      "year": 2024,
      "mcu_revenue_yuan": 500000000,
      "mcu_gross_margin": 0.35,
      "mcu_share_pct": 25.0,
      "confidence": "medium",
      "quote": "原文摘录（≤100字）",
      "note": "额外说明"
    }
  ]
}

若 found=false：{"found": false, "reason": "原因说明"}
"""

COMPANY_HINTS = {
    "002180": "公司是纳思达（002180），旗下极海半导体（Geehy）是MCU子公司。寻找极海/Geehy的单独营收数字，不要使用集团264亿量级的总营收。",
    "300077": "公司是国民技术（300077），产品包括MCU和安全芯片（SE）。寻找MCU单独营收或MCU占比，CLAUDE.md提示历史IR记录中有'MCU占总收入约27%'这类表述。",
    "688385": "公司是复旦微电子（688385），MCU口径为'智能电表芯片'产品线，年报区分芯片/模组，需从IR记录确认纯芯片口径的MCU营收。",
    "688018": "公司是乐鑫科技（688018），MCU口径为年报中的'芯片收入'（不含模组/解决方案）。寻找芯片收入单独数字或芯片/模组比例。",
    "688595": "公司是芯海科技（688595），MCU与模拟混合，寻找MCU产品线单独营收或MCU在总收入中的占比。",
}

# ── CNINFO API ────────────────────────────────────────────────────────────────

def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    if CNINFO_COOKIE:
        s.headers["Cookie"] = CNINFO_COOKIE
    return s


def _cninfo_query(session: requests.Session, symbol: str, org_id: str,
                   market: str, category: str, searchkey: str,
                   year_start: int, year_end: int,
                   debug: bool = False) -> list[dict]:
    """单次 CNINFO category 查询，返回公告列表。

    关键参数说明：
    - searchkey: CNINFO 仅支持单个关键词的简单子串匹配，不支持 OR/AND 语法
      → 多关键词需调用方分次传入，或留空后客户端过滤标题
    - seDate: 格式 "YYYY-MM-DD~YYYY-MM-DD"（无空格）
    - tabName: "fulltext" 适用于年报等，IR 记录通常也兼容
    """
    column = "sse" if market == "sse" else "szse"
    results = []
    page = 1

    while page <= 10:
        payload = {
            "stock":     f"{symbol},{org_id}",
            "tabName":   "fulltext",
            "pageSize":  30,
            "pageNum":   page,
            "column":    column,
            "category":  category,
            "plate":     "",
            "seDate":    f"{year_start}-01-01~{year_end}-12-31",  # 无空格
            "searchkey": searchkey,
            "secid":     "",
            "sortName":  "pubdate",
            "sortType":  "desc",
            "isHLtitle": True,
        }
        try:
            resp = session.post(QUERY_URL, data=payload, timeout=30)
            resp.raise_for_status()
            d = resp.json()
        except Exception as exc:
            log.warning("  API error (cat=%s key=%r page=%d): %s",
                        category or "ALL", searchkey, page, exc)
            break

        if debug and page == 1:
            total = d.get("totalAnnouncement", "?")
            log.debug("  [DEBUG] cat=%r key=%r → totalAnnouncement=%s",
                      category, searchkey, total)
            sample = (d.get("announcements") or [])[:2]
            for s in sample:
                log.debug("    sample: %s", s.get("announcementTitle", "")[:80])

        announcements = d.get("announcements") or []
        if not announcements:
            break
        results.extend(announcements)

        total = d.get("totalAnnouncement", 0)
        if page * 30 >= total:
            break
        page += 1
        time.sleep(0.4)

    return results


def query_ir_announcements(session: requests.Session, symbol: str, org_id: str,
                            market: str, year_start: int, year_end: int,
                            debug: bool = False) -> list[dict]:
    """查询投资者关系活动记录公告列表。

    策略：
    1. 用 CATEGORY_IR 拉全量 IR 记录（不限关键词），客户端过滤标题
    2. 再用各关键词+全量 category 补充捞漏网之鱼
    """
    raw: list[dict] = []

    # Pass 1: 全量 IR 类别（不设 searchkey，避免 OR 语法陷阱）
    raw += _cninfo_query(session, symbol, org_id, market,
                         category=CATEGORY_IR, searchkey="",
                         year_start=year_start, year_end=year_end, debug=debug)

    # Pass 2: 全量 category，逐个关键词搜索（单词匹配更可靠）
    for kw in ["MCU", "微控制器", "极海", "Geehy", "调研", "投资者关系活动"]:
        raw += _cninfo_query(session, symbol, org_id, market,
                             category="", searchkey=kw,
                             year_start=year_start, year_end=year_end, debug=debug)
        time.sleep(0.3)

    # 客户端过滤：标题包含 IR 相关关键词
    IR_TITLE_KW = [
        "投资者关系", "调研", "业绩说明", "互动", "路演",
        "MCU", "微控制器", "Geehy", "极海",
    ]
    EXCLUDE_KW = ["摘要", "英文", "English", "更正", "取消", "撤销"]

    results: list[dict] = []
    seen: set[str] = set()

    for ann in raw:
        title    = ann.get("announcementTitle", "")
        adjunct  = ann.get("adjunctUrl", "")
        pub_date = ann.get("announcementTime", "")

        if adjunct in seen:
            continue
        if any(ex in title for ex in EXCLUDE_KW):
            continue
        if not any(kw in title for kw in IR_TITLE_KW):
            continue

        yr_m = re.search(r"20(\d{2})", pub_date)
        if yr_m:
            pub_yr = int("20" + yr_m.group(1))
            if not (year_start <= pub_yr <= year_end):
                continue

        seen.add(adjunct)
        results.append({
            "title":    title,
            "adjunct":  adjunct,
            "pub_date": pub_date,
            "category": ann.get("announcementTypeName", ""),
        })

    return results


def download_pdf(session: requests.Session, adjunct_url: str) -> Optional[bytes]:
    """下载PDF文件内容。"""
    if not adjunct_url:
        return None
    url = f"https://static.cninfo.com.cn/{adjunct_url}"
    try:
        resp = session.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        log.warning("  PDF download failed: %s", exc)
        return None


# ── PDF 内容提取 ──────────────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_bytes: bytes, max_pages: int = MAX_PAGES_PER_DOC) -> str:
    try:
        import pdfplumber
    except ImportError:
        sys.exit("请先安装: pip install pdfplumber")

    parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages[:max_pages]:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
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


# ── LLM 提取 ──────────────────────────────────────────────────────────────────

def call_llm_deepseek(text: str, symbol: str, api_key: str) -> Optional[dict]:
    """DeepSeek V3 提取（文本路径）。"""
    hint   = COMPANY_HINTS.get(symbol, f"股票代码: {symbol}")
    system = SYSTEM_PROMPT.format(company_hint=hint)
    if len(text) > 12000:
        text = text[:12000] + "\n…（已截断）"
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": f"以下是投资者关系文档内容：\n\n{text}"},
            ],
            max_tokens=1024, temperature=0.1,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content.strip())
    except Exception as exc:
        log.warning("  DeepSeek call failed: %s", exc)
        return None


def call_llm_gemini(text: str, symbol: str, api_key: str) -> Optional[dict]:
    """Gemini 2.0 Flash 提取（文本路径，IR记录通常是纯文字，此路径足够）。"""
    hint   = COMPANY_HINTS.get(symbol, f"股票代码: {symbol}")
    system = SYSTEM_PROMPT.format(company_hint=hint)
    if len(text) > 30000:   # Gemini 1M context — IR docs much shorter
        text = text[:30000] + "\n…（已截断）"
    try:
        import google.generativeai as genai
        import re as _re
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            "gemini-2.0-flash",
            system_instruction=system,
        )
        prompt = (
            "以下是投资者关系文档内容，请提取MCU数据并输出JSON：\n\n" + text
        )
        resp = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json",
                                "temperature": 0.1, "max_output_tokens": 1024},
        )
        raw = resp.text.strip()
        raw = _re.sub(r"^```json\s*|\s*```$", "", raw, flags=_re.MULTILINE)
        return json.loads(raw)
    except Exception as exc:
        log.warning("  Gemini call failed: %s", exc)
        return None


def call_llm(text: str, symbol: str, api_key: str,
             model: str = "deepseek") -> Optional[dict]:
    """统一 LLM 调用接口。model: 'deepseek' | 'gemini'"""
    if model == "gemini":
        result = call_llm_gemini(text, symbol, api_key)
        if result:
            result["_model"] = "gemini-2.0-flash"
            return result
        return None
    # deepseek (default)
    result = call_llm_deepseek(text, symbol, api_key)
    if result:
        result["_model"] = "deepseek-chat"
    return result


# ── 结果写入 mcu_known_data.json ─────────────────────────────────────────────

def merge_into_known_data(symbol: str, records: list[dict], source_title: str,
                          pub_date: str, dry_run: bool = False) -> int:
    """将 LLM 提取结果写入 mcu_known_data.json，优先级低于年报数据。"""
    path = HERE / "mcu_known_data.json"
    known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    co_data = known.setdefault(symbol, {})

    written = 0
    for rec in records:
        year = str(rec.get("year", ""))
        if not year or not re.fullmatch(r"20\d{2}", year):
            log.warning("    跳过无效年份: %s", rec)
            continue
        mcu_yuan = rec.get("mcu_revenue_yuan")
        if not mcu_yuan:
            continue

        existing = co_data.get(year, {})
        # 不覆盖已有年报数据（data_type=reported）
        if existing.get("data_type") == "reported":
            log.info("    [%s][%s] 已有reported数据，跳过IR覆盖", symbol, year)
            continue

        entry = {
            "mcu_revenue_yuan": float(mcu_yuan),
            "data_type":  "ir_record",
            "confidence": rec.get("confidence", "low"),
            "source":     f"{symbol} IR记录 {pub_date[:10]} 《{source_title[:60]}》 — {rec.get('quote','')[:80]}",
        }
        if rec.get("mcu_gross_margin") is not None:
            entry["mcu_gross_margin"] = float(rec["mcu_gross_margin"])
        if rec.get("mcu_share_pct") is not None:
            entry["mcu_share_pct_ir"] = float(rec["mcu_share_pct"])
        if rec.get("note"):
            entry["note"] = rec["note"]

        if dry_run:
            log.info("    [DRY-RUN] 会写入 [%s][%s]: %s元", symbol, year, mcu_yuan)
        else:
            co_data[year] = entry
            log.info("    ✓ 写入 [%s][%s]: %s元 (conf=%s)",
                     symbol, year, mcu_yuan, entry["confidence"])
        written += 1

    if not dry_run and written > 0:
        path.write_text(json.dumps(known, ensure_ascii=False, indent=2), encoding="utf-8")

    return written


# ── BQ 上传（可选）────────────────────────────────────────────────────────────

def maybe_upload_to_bq(symbol: str, year: str, record: dict, source: str):
    try:
        import bq_writer
        if not bq_writer.is_available():
            return
        import json as _json
        from datetime import datetime, timezone
        row = {
            "symbol": symbol,
            "year": int(year),
            "report_type": "IR记录",
            "extraction_method": "llm_ir",
            "mcu_revenue_yuan": record.get("mcu_revenue_yuan"),
            "mcu_gross_margin": record.get("mcu_gross_margin"),
            "mcu_data_type": "ir_record",
            "mcu_confidence": record.get("confidence", "low"),
            "mcu_source": source,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }
        bq_writer.upsert_rows([row])
        log.info("    → BQ 上传成功")
    except Exception as exc:
        log.debug("    BQ 上传跳过: %s", exc)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="从CNINFO IR记录提取MCU营收数据")
    parser.add_argument("--symbols", nargs="+", default=TARGET_SYMBOLS,
                        help="股票代码列表，默认: 002180 300077 688385 688018 688595")
    parser.add_argument("--year-start", type=int, default=YEAR_START_DEFAULT)
    parser.add_argument("--year-end",   type=int, default=YEAR_END_DEFAULT)
    parser.add_argument("--dry-run",    action="store_true",
                        help="只列出文件，不提取/写入")
    parser.add_argument("--max-docs",   type=int, default=MAX_DOCS_PER_COMPANY,
                        help="每家公司最多处理文件数 (默认30)")
    parser.add_argument("--model", choices=["deepseek", "gemini"], default="deepseek",
                        help="LLM 提取模型 (默认: deepseek)")
    parser.add_argument("--debug", action="store_true",
                        help="打印 CNINFO API 原始响应样本，用于排查 0 条问题")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # API Key 解析
    def get_key(env_var: str, secret_id: str) -> Optional[str]:
        if v := os.environ.get(env_var):
            return v
        try:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
            project = os.environ.get("GCP_PROJECT", "st-china-ai-force")
            name = f"projects/{project}/secrets/{secret_id}/versions/latest"
            return client.access_secret_version(name=name).payload.data.decode()
        except Exception:
            return None

    if args.model == "gemini":
        api_key = get_key("VITE_GEMINI_API_KEY", "VITE_GEMINI_API_KEY")
        if not api_key and not args.dry_run:
            log.error("未设置 VITE_GEMINI_API_KEY 环境变量")
            sys.exit(1)
    else:
        api_key = (get_key("VITE_DEEPSEEK_API_KEY", "VITE_DEEPSEEK_API_KEY")
                   or get_key("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"))
        if not api_key and not args.dry_run:
            log.error("未设置 VITE_DEEPSEEK_API_KEY 环境变量")
            sys.exit(1)

    log.info("模型: %s", args.model)
    session = build_session()
    total_written = 0

    for symbol in args.symbols:
        info = COMPANY_INFO.get(symbol)
        if not info:
            log.warning("未知股票代码: %s", symbol)
            continue

        log.info("━━ %s (%s) ━━", info["name"], symbol)
        announcements = query_ir_announcements(
            session, symbol, info["orgId"], info["market"],
            args.year_start, args.year_end, debug=args.debug
        )
        log.info("  找到 %d 条 IR 公告", len(announcements))

        if not announcements:
            log.info("  → 0条，建议运行: python fetch_ir_records.py --symbols %s --debug", symbol)
            log.info("     查看 API 原始响应，确认 category/orgId 是否正确")
            continue

        processed = 0
        for ann in announcements[:args.max_docs]:
            title    = ann["title"]
            adjunct  = ann["adjunct"]
            pub_date = ann["pub_date"]
            log.info("  ▸ [%s] %s", pub_date[:10], title[:70])

            if args.dry_run:
                log.info("    [DRY-RUN] 会下载 %s", adjunct)
                processed += 1
                continue

            pdf_bytes = download_pdf(session, adjunct)
            if not pdf_bytes:
                continue

            text = extract_text_from_pdf(pdf_bytes)
            if len(text.strip()) < 100:
                log.info("    PDF 文本过短，跳过")
                continue

            # 快速关键词预筛：IR文档很多，先检查是否含MCU相关词
            has_kw = any(kw in text for kw in [
                "MCU", "微控制器", "极海", "Geehy", "智能电表", "芯片收入",
                "控制芯片", "安全芯片", "国民技术MCU",
            ])
            if not has_kw:
                log.info("    无MCU关键词，跳过LLM")
                continue

            result = call_llm(text, symbol, api_key, model=args.model)
            if not result or not result.get("found"):
                reason = result.get("reason", "无数字") if result else "LLM失败"
                log.info("    未提取到数据: %s", reason)
                continue

            records = result.get("records", [])
            log.info("    提取到 %d 条记录", len(records))
            written = merge_into_known_data(symbol, records, title, pub_date, args.dry_run)
            total_written += written

            for rec in records:
                yr = str(rec.get("year", ""))
                if yr and rec.get("mcu_revenue_yuan"):
                    source_str = (f"{symbol} IR记录 {pub_date[:10]} "
                                  f"《{title[:60]}》— {rec.get('quote','')[:80]}")
                    maybe_upload_to_bq(symbol, yr, rec, source_str)

            processed += 1
            time.sleep(1.0)   # 避免触发速率限制

        log.info("  处理 %d 份文档", processed)

    log.info("━━ 完成：共写入 %d 条数据 ━━", total_written)
    if total_written > 0 and not args.dry_run:
        log.info("请运行 python validate_data.py 确认数据质量")


if __name__ == "__main__":
    main()
