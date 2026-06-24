from __future__ import annotations

# HTTP wrapper that exposes the entity matcher as a Cloud Run service

from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .run import run_entity_matcher


app = FastAPI(title="Campercontact Entity Matcher")


class EntityMatcherRequest(BaseModel):
    profile_ids: Optional[list[str]] = Field(
        default=None,
        description="Optional list of Campercontact sitecodes/profile IDs to match.",
    )
    gap_detector_run_id: Optional[str] = Field(
        default=None,
        description="Optional Gap Detector run ID. When provided, only Source Finder rows from that run are matched.",
    )
    limit: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional max number of profiles to process after source evidence loading.",
    )
    write_bigquery: bool = Field(
        default=False,
        description="If true, write Entity Matcher output to BigQuery. If false, dry run only.",
    )
    append: bool = Field(
        default=True,
        description="If true, append to output table. If false and write_bigquery=true, truncate/replace table output.",
    )
    use_gap_filter: bool = Field(
        default=True,
        description="If true, infer relevant fields from Gap Detector recommended_actions.",
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "entity-matcher-agent"}


@app.post("/run-entity-matcher")
def run_entity_matcher_endpoint(payload: EntityMatcherRequest) -> dict[str, Any]:
    return run_entity_matcher(
        profile_ids=payload.profile_ids,
        gap_detector_run_id=payload.gap_detector_run_id,
        limit=payload.limit,
        write=payload.write_bigquery,
        append=payload.append,
        use_gap_filter=payload.use_gap_filter,
    )
