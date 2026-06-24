"""FastAPI HTTP service wrapper for the entity matcher.

Run from the agents/ directory:
    uvicorn entity_matcher.app:app --host 0.0.0.0 --port 8080

The gap detector calls POST /run-entity-matcher on this service.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI
from google.cloud import bigquery
from pydantic import BaseModel

from .comparators import NO_DATA
from .config import BIGQUERY_PROJECT, SOURCE_TABLE
from .io_layer import (
    combine_profiles_with_sources,
    load_cc_profiles,
    load_sources_for_profiles,
    write_output,
)
from .matcher import match_batch

log = logging.getLogger(__name__)
app = FastAPI()


class RunEntityMatcherRequest(BaseModel):
    gap_detector_run_id: Optional[str] = None
    write_bigquery: bool = True
    append: bool = True
    limit: Optional[int] = None


@app.get("/health")
def health():
    return {"status": "ok", "agent": "entity-matcher"}


@app.post("/run-entity-matcher")
def run_entity_matcher(req: RunEntityMatcherRequest):
    client = bigquery.Client(project=BIGQUERY_PROJECT)

    # When scoped to a run: get only the sitecodes that SF processed in this run.
    sitecodes: list[str] | None = None
    if req.gap_detector_run_id:
        sitecodes = _get_sitecodes_for_run(client, req.gap_detector_run_id)
        if not sitecodes:
            log.warning("No source finder output found for run %s", req.gap_detector_run_id)
            return {
                "status": "no_sources",
                "gap_detector_run_id": req.gap_detector_run_id,
                "profiles_loaded": 0,
                "results_written": 0,
            }

    cc_rows = load_cc_profiles(
        client, sitecodes=sitecodes, limit=None if sitecodes else req.limit
    )
    if not cc_rows:
        return {
            "status": "no_profiles",
            "gap_detector_run_id": req.gap_detector_run_id,
            "profiles_loaded": 0,
            "results_written": 0,
        }

    effective_sitecodes = sitecodes or [str(r.get("sitecode") or "") for r in cc_rows]
    source_rows = load_sources_for_profiles(
        client, effective_sitecodes, gap_detector_run_id=req.gap_detector_run_id
    )
    combined = combine_profiles_with_sources(cc_rows, source_rows)
    profiles_with_sources = [(cc, srcs) for cc, srcs in combined if srcs]

    results = match_batch(profiles_with_sources)

    # Stamp run_id so confidence agent can filter by it.
    if req.gap_detector_run_id:
        for r in results:
            r["gap_detector_run_id"] = req.gap_detector_run_id

    written = 0
    if req.write_bigquery and results:
        write_output(client, results, dry_run=False, truncate=not req.append)
        written = len([r for r in results if r["verification_status"] != NO_DATA])

    return {
        "status": "completed",
        "gap_detector_run_id": req.gap_detector_run_id,
        "profiles_loaded": len(cc_rows),
        "profiles_with_sources": len(profiles_with_sources),
        "results_total": len(results),
        "results_written": written,
    }


def _get_sitecodes_for_run(client: bigquery.Client, gap_detector_run_id: str) -> list[str]:
    """Return distinct sitecodes that have source finder output for this run."""
    dataset, tbl = SOURCE_TABLE.split(".", 1)
    table = f"`{BIGQUERY_PROJECT}.{dataset}.{tbl}`"
    params = [bigquery.ScalarQueryParameter("run_id", "STRING", gap_detector_run_id)]
    query = f"""
        SELECT DISTINCT CAST(profile_id AS STRING) AS sitecode
        FROM {table}
        WHERE gap_detector_run_id = @run_id
          AND extraction_status = 'success'
    """
    try:
        rows = list(
            client.query(
                query, job_config=bigquery.QueryJobConfig(query_parameters=params)
            ).result()
        )
        return [row["sitecode"] for row in rows]
    except Exception as exc:
        log.warning("_get_sitecodes_for_run failed (%s), returning empty list", exc)
        return []
