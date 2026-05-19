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
from pathlib import Path

from flask import Flask, Response, jsonify, send_file, send_from_directory

import bq_writer

log = logging.getLogger(__name__)
app = Flask(__name__)
HERE = Path(__file__).parent


# ── Static files ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file(HERE / "dashboard.html")


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
