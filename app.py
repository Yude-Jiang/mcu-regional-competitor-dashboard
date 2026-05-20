#!/usr/bin/env python3
"""app.py — Flask server for the MCU Regional Competitor Dashboard.

Routes:
  GET  /                  dashboard.html
  GET  /data.json         static financial data (local cache)
  GET  /companies_meta.json
  GET  /api/doc-status    BigQuery PDF document status matrix (no auth required)

Phase 3+ routes (not yet implemented):
  POST /api/refresh       trigger CNINFO download + LLM extraction
  GET  /api/refresh/stream  SSE progress feed
  POST /api/ask           AI Q&A (DeepSeek / Gemini)
"""

import json
import logging
import os
import re
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

import bq_writer

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


# ── Placeholder stubs (Phase 3+) ──────────────────────────────────────────────

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    return jsonify({"error": "not_implemented",
                    "message": "Report download pipeline coming in Phase 3"}), 501


@app.route("/api/ask", methods=["POST"])
def api_ask():
    return jsonify({"error": "not_implemented",
                    "message": "AI Q&A coming in Phase 5"}), 501


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
