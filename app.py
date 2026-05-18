#!/usr/bin/env python3
"""
app.py — small Flask app to serve dashboard.html and static data
"""

from flask import Flask, send_file, send_from_directory, jsonify
from pathlib import Path

app = Flask(__name__)

HERE = Path(__file__).parent

@app.route("/")
def index():
    return send_file(HERE / "dashboard.html")

@app.route("/data.json")
def data_json():
    return send_from_directory(HERE, "data.json")

@app.route("/profiles_xq.json")
def profiles_xq():
    return send_from_directory(HERE, "profiles_xq.json")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
