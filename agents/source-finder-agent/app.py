from __future__ import annotations

# Flask endpoints for the source finder, run one profile or drain the pending queue

import os
from flask import Flask, jsonify, request

from source_finder.core import run_source_finder
from source_finder.queue_runner import run_pending_source_finder


app = Flask(__name__)


@app.get("/")
def root():
    return jsonify({
        "service": "source-finder-agent",
        "status": "running"
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/run-source-finder")
def run_source_finder_endpoint():
    payload = request.get_json(silent=True) or {}

    profile_id = payload.get("profile_id") or payload.get("sitecode")

    if not profile_id:
        return jsonify({
            "error": "Missing required field: profile_id or sitecode"
        }), 400

    try:
        result = run_source_finder(
            profile_id=str(profile_id),
            gap_detector_run_id=str(payload.get("gap_detector_run_id", "")),
            page_size=int(payload.get("page_size", 10)),
            max_candidate_urls=int(payload.get("max_candidate_urls", 20)),
            export_csv=bool(payload.get("export_csv", False)),
            write_bigquery=bool(payload.get("write_bigquery", True)),
        )

        return jsonify(result), 200

    except Exception as exc:
        return jsonify({
            "error": str(exc),
            "profile_id": str(profile_id)
        }), 500


@app.post("/run-pending-source-finder")
def run_pending_source_finder_endpoint():
    payload = request.get_json(silent=True) or {}

    try:
        result = run_pending_source_finder(
            limit=int(payload.get("limit", 5)),
            write_bigquery=bool(payload.get("write_bigquery", True)),
            gap_detector_run_id=payload.get("gap_detector_run_id"),
        )
        return jsonify(result), 200

    except Exception as exc:
        return jsonify({
            "error": str(exc)
        }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
