#!/usr/bin/env python3
"""app.py — Flask server for the MCU Regional Competitor Dashboard."""

from flask import Flask, send_file, send_from_directory
from pathlib import Path

app = Flask(__name__)
HERE = Path(__file__).parent


@app.route("/")
def index():
    return send_file(HERE / "dashboard.html")


@app.route("/data.json")
def data_json():
    return send_from_directory(HERE, "data.json", mimetype="application/json")


@app.route("/companies_meta.json")
def companies_meta():
    return send_from_directory(HERE, "companies_meta.json", mimetype="application/json")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
