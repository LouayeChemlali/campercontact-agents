from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from pipeline_runner import run_hint_generator, refresh_prioritized_candidates
from google.cloud import bigquery

app = FastAPI(title="Campercontact Hint Generator")


class HintGeneratorRequest(BaseModel):
    gap_detector_run_id: Optional[str] = Field(default=None)
    profile_id: Optional[str] = Field(default=None)
    limit: Optional[int] = Field(default=20, ge=1)
    refresh_prioritization: bool = Field(default=True)
    write_bigquery: bool = Field(default=True)


class PrioritizationRequest(BaseModel):
    gap_detector_run_id: Optional[str] = Field(default=None)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "hint-generator-agent"}


@app.post("/run-hint-prioritization")
def run_prioritization_endpoint(payload: PrioritizationRequest) -> dict[str, Any]:
    client = bigquery.Client()
    return refresh_prioritized_candidates(client=client, gap_detector_run_id=payload.gap_detector_run_id)


@app.post("/run-hint-generator")
def run_hint_generator_endpoint(payload: HintGeneratorRequest) -> dict[str, Any]:
    return run_hint_generator(
        gap_detector_run_id=payload.gap_detector_run_id,
        profile_id=payload.profile_id,
        limit=payload.limit,
        refresh_prioritization=payload.refresh_prioritization,
        write_bigquery=payload.write_bigquery,
    )
