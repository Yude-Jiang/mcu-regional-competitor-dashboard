#!/usr/bin/env python3
"""app.py — Flask server for the MCU Regional Competitor Dashboard.

Routes:
  GET  /                  dashboard.html
  GET  /admin             admin.html
  GET  /data.json         static financial data (local cache)
  GET  /companies_meta.json
  GET  /api/doc-status    BigQuery PDF document status matrix
  POST /api/company/add   add new company to companies_meta.json
  POST /api/ask           AI Q&A (DeepSeek V3 / Gemini 2.0 Flash)
  POST /api/refresh       [Phase 3 stub] trigger CNINFO download pipeline
"""

import json
import logging
import os
import re
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

import bq_writer

# ── API key helper ─────────────────────────────────────────────────────────────

def _get_api_key(env_var: str, secret_id: str) -> str | None:
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

log = logging.getLogger(__name__)
app = Flask(__name__)
HERE = Path(__file__).parent


# ── Static files ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file(HERE / "dashboard.html")


@app.route("/admin")
def admin():
    return send_file(HERE / "admin.html")


@app.route("/data.json")
def data_json():
    return send_from_directory(HERE, "data.json", mimetype="application/json")


@app.route("/companies_meta.json")
def companies_meta():
    return send_from_directory(HERE, "companies_meta.json", mimetype="application/json")


@app.route("/fx_rates.json")
def fx_rates():
    return send_from_directory(HERE, "fx_rates.json", mimetype="application/json")


@app.route("/profiles_xq.json")
def profiles_xq():
    return send_from_directory(HERE, "profiles_xq.json", mimetype="application/json")


# ── API: document status matrix ───────────────────────────────────────────────

@app.route("/api/doc-status")
def api_doc_status():
    """Return PDF document status for all companies × years × report types.

    Response shape:
    {
      "available": true,
      "matrix": {
        "603986": {
          "2024": {
            "年报": { "download_status": "downloaded", "extraction_status": "extracted",
                      "file_size_kb": 8240, "downloaded_at": "2025-05-01T10:00:00+00:00" }
          }
        }
      }
    }
    """
    available = bq_writer.is_available()
    matrix = bq_writer.get_doc_status_matrix() if available else {}
    return jsonify({"available": available, "matrix": matrix})


# ── API: add new company ───────────────────────────────────────────────────────

REQUIRED_FIELDS = ("symbol", "name_cn", "name_en", "market", "mcu_strategy", "mcu_confidence")
VALID_MARKETS = {"SH", "SZ", "STAR", "ChiNext"}
VALID_STRATEGIES = {
    "total_revenue", "total_proxy", "segment_reported",
    "segment_estimated", "subsidiary_geehy", "estimated",
}
VALID_CONFIDENCES = {"high", "medium", "low"}


@app.route("/api/company/add", methods=["POST"])
def api_company_add():
    """Validate and append a new company to companies_meta.json and data.json."""
    body = request.get_json(force=True, silent=True) or {}

    # Validate required fields present
    missing = [f for f in REQUIRED_FIELDS if not body.get(f)]
    if missing:
        return jsonify({"error": "missing_fields", "fields": missing}), 400

    symbol = str(body["symbol"]).strip()
    if not re.fullmatch(r"\d{6}", symbol):
        return jsonify({"error": "invalid_symbol", "message": "股票代码必须为6位数字"}), 400

    market = body["market"]
    if market not in VALID_MARKETS:
        return jsonify({"error": "invalid_market", "valid": sorted(VALID_MARKETS)}), 400

    strategy = body["mcu_strategy"]
    if strategy not in VALID_STRATEGIES:
        return jsonify({"error": "invalid_strategy", "valid": sorted(VALID_STRATEGIES)}), 400

    confidence = body["mcu_confidence"]
    if confidence not in VALID_CONFIDENCES:
        return jsonify({"error": "invalid_confidence", "valid": sorted(VALID_CONFIDENCES)}), 400

    # Load companies_meta.json
    meta_path = HERE / "companies_meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.exception("Failed to read companies_meta.json")
        return jsonify({"error": "read_error", "message": str(exc)}), 500

    if symbol in meta:
        return jsonify({"error": "duplicate_symbol",
                        "message": f"{symbol} 已存在于 companies_meta.json"}), 400

    # Build new company dict
    company = {
        "name_cn": str(body["name_cn"]).strip(),
        "name_en": str(body["name_en"]).strip(),
        "symbol": symbol,
        "market": market,
        "founded_year": int(body["founded_year"]) if body.get("founded_year") else None,
        "listed_year": int(body["listed_year"]) if body.get("listed_year") else None,
        "core_license": str(body.get("core_license", "")).strip() or None,
        "foundry_fab": str(body.get("foundry_fab", "")).strip() or None,
        "mcu_strategy": strategy,
        "mcu_multiplier": float(body["mcu_multiplier"]) if body.get("mcu_multiplier") is not None and body.get("mcu_multiplier") != "" else None,
        "mcu_confidence": confidence,
        "mcu_note": str(body.get("mcu_note", "")).strip() or None,
    }
    if body.get("cninfo_org_id"):
        company["cninfo_org_id"] = str(body["cninfo_org_id"]).strip()

    # Write companies_meta.json
    meta[symbol] = company
    try:
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        log.exception("Failed to write companies_meta.json")
        return jsonify({"error": "write_error", "message": str(exc)}), 500

    # Add skeleton to data.json
    data_path = HERE / "data.json"
    if data_path.exists():
        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
            if "companies" not in data:
                data["companies"] = {}
            if symbol not in data["companies"]:
                data["companies"][symbol] = {
                    "name_cn": company["name_cn"],
                    "name_en": company["name_en"],
                    "market": market,
                    "annual": {},
                }
                data_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        except Exception as exc:
            log.warning("Could not update data.json: %s", exc)
            # Non-fatal — companies_meta.json was already written

    return jsonify({
        "ok": True,
        "company": company,
        "next_steps": [
            "手动添加 symbol 到 fetch_mcu_data.py 的 MCU_SYMBOLS 列表",
            "手动添加 symbol 到 fetch_yjbb_quarterly.py 的 MCU_SYMBOLS 列表",
            "手动添加 symbol 到 validate_data.py 的 MCU_SYMBOLS 列表",
        ],
    }), 201


# ── API: AI Q&A ───────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """你是一位专注于中国大陆MCU（微控制器）上市公司的半导体行业竞争情报分析师。
你的数据来源是11家A股MCU公司的年报财务数据（2018–2025）和AI提取的MCU分段营收。
回答要简洁、数据驱动，尽量引用具体数字。使用中文回答。

数据说明：
- MCU营收：部分来自年报直接披露（segment_reported），部分通过系数推算（total_proxy/total_revenue）
- 置信度：high=年报直接披露，medium=估算，low=粗估
- 货币：USD（按当年平均汇率换算）

你掌握的公司数据：
{context}
"""

def _load_fx() -> dict[int, float]:
    p = HERE / "fx_rates.json"
    if p.exists():
        return {int(k): v for k, v in json.loads(p.read_text())["CNY_USD"].items()}
    return {2018:6.6174,2019:6.8985,2020:6.8976,2021:6.4515,
            2022:6.7261,2023:7.0809,2024:7.1900,2025:7.2200}

FX = _load_fx()

COMPANY_ORDER = [
    "603986","300327","688380","300077","688279",
    "002180","688385","688766","688595","688391","688018",
]


def _build_context() -> str:
    """Build a compact financial summary from data.json for the LLM context."""
    path = HERE / "data.json"
    meta_path = HERE / "companies_meta.json"
    if not path.exists():
        return "（data.json 尚未生成，请先运行 smart_sync.py）"

    try:
        data = json.loads(path.read_text())
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    except Exception:
        return "（数据文件读取失败）"

    companies = data.get("companies", {})
    lines: list[str] = []

    for sym in COMPANY_ORDER:
        co = companies.get(sym)
        if not co:
            continue
        m = co.get("meta", {}) or meta.get(sym, {})
        name = m.get("name_cn", sym)
        name_en = m.get("name_en", "")
        strategy = m.get("mcu_strategy", "")
        confidence = m.get("mcu_confidence", "")
        fin = co.get("financials", {})

        lines.append(f"\n【{name} ({name_en}) · {sym}】")
        lines.append(f"  MCU口径: {strategy}  置信度: {confidence}")

        yr_rows = []
        for yr in sorted(fin.keys(), reverse=True)[:5]:  # last 5 years
            f = fin[yr]
            rev  = f.get("total_revenue_musd")
            mcu  = f.get("mcu_revenue_musd")
            gm   = f.get("gross_margin_pct")
            rd   = f.get("rd_expense_musd")
            rd_r = f.get("rd_pct")
            ni_y = f.get("net_income_yoy_pct")
            emp  = f.get("employee_count")

            parts = [f"{yr}年:"]
            if rev  is not None: parts.append(f"总营收${rev:.1f}M")
            if mcu  is not None: parts.append(f"MCU${mcu:.1f}M")
            if gm   is not None: parts.append(f"毛利率{gm:.1f}%")
            if rd   is not None: parts.append(f"研发${rd:.1f}M")
            if rd_r is not None: parts.append(f"研发率{rd_r:.1f}%")
            if ni_y is not None: parts.append(f"净利润YoY{ni_y:+.1f}%")
            if emp  is not None: parts.append(f"员工{emp}人")
            yr_rows.append("  " + " | ".join(parts))

        lines.extend(yr_rows if yr_rows else ["  （暂无财务数据）"])

    return "\n".join(lines) if lines else "（暂无数据）"


def _call_deepseek(question: str, context: str, api_key: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT.format(context=context)},
            {"role": "user",   "content": question},
        ],
        max_tokens=1024,
        temperature=0.3,
    )
    return resp.choices[0].message.content.strip()


def _call_gemini(question: str, context: str, api_key: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    prompt = _SYSTEM_PROMPT.format(context=context) + f"\n\n用户问题：{question}"
    resp = model.generate_content(prompt)
    return resp.text.strip()


@app.route("/api/ask", methods=["POST"])
def api_ask():
    """AI Q&A over MCU competitor financials.

    Request:  {"question": "兆易创新2024年MCU营收是多少？"}
    Response: {"answer": "...", "model": "deepseek-chat", "ok": true}
    """
    body = request.get_json(force=True, silent=True) or {}
    question = str(body.get("question", "")).strip()
    if not question:
        return jsonify({"error": "missing_question", "message": "请提供 question 字段"}), 400
    if len(question) > 2000:
        return jsonify({"error": "question_too_long"}), 400

    context = _build_context()

    # Try DeepSeek first, Gemini fallback
    deepseek_key = _get_api_key("VITE_DEEPSEEK_API_KEY", "VITE_DEEPSEEK_API_KEY")
    gemini_key   = _get_api_key("VITE_GEMINI_API_KEY",   "VITE_GEMINI_API_KEY")

    if deepseek_key:
        try:
            answer = _call_deepseek(question, context, deepseek_key)
            return jsonify({"ok": True, "answer": answer, "model": "deepseek-chat"})
        except Exception as exc:
            log.warning("DeepSeek failed: %s — trying Gemini", exc)

    if gemini_key:
        try:
            answer = _call_gemini(question, context, gemini_key)
            return jsonify({"ok": True, "answer": answer, "model": "gemini-2.0-flash"})
        except Exception as exc:
            log.warning("Gemini failed: %s", exc)
            return jsonify({"error": "llm_error", "message": str(exc)}), 502

    return jsonify({
        "error": "no_api_key",
        "message": "未配置 VITE_DEEPSEEK_API_KEY 或 VITE_GEMINI_API_KEY",
    }), 503


# ── Phase 3 stub ───────────────────────────────────────────────────────────────

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    return jsonify({"error": "not_implemented",
                    "message": "Report download pipeline coming in Phase 3"}), 501


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
