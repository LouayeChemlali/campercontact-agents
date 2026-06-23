"""HTTP client for the Gap Detector Cloud Run service."""

import logging
from dataclasses import dataclass

import requests

from config import GD_URL

log = logging.getLogger(__name__)

# Cloud Run services scale to zero when idle. A cold start can take 30-60 s,
# so we give 90 s for the handoff before declaring a timeout.
_REQUEST_TIMEOUT = 90


@dataclass
class PipelineRunResult:
    """Structured result returned to the caller after a trigger attempt."""

    success: bool
    run_id: str | None = None
    error_message: str | None = None
    # Non-None when the pipeline started but with a degraded configuration,
    # for example when the Gap Detector ignored the profile_ids field.
    warning: str | None = None


def trigger_pipeline(
    profile_ids: list[str],
    *,
    source_finder_limit: int = 3,
    entity_matcher_limit: int = 3,
    hint_generator_limit: int = 10,
    write_bigquery: bool = True,
) -> PipelineRunResult:
    """
    Start the pipeline for the given profile IDs.

    Sends profile_ids in the payload. If the Gap Detector returns 422
    (the field is not yet supported server-side), retries without it,
    logs a warning, and returns success with a warning message so the
    UI can inform the moderator without blocking the run.
    """
    if not GD_URL:
        return PipelineRunResult(
            success=False,
            error_message=(
                "Pipeline trigger is not configured. "
                "Set GD_URL in the .env file."
            ),
        )

    payload: dict = {
        "profile_ids": profile_ids,
        "trigger_source_finder": True,
        "source_finder_limit": source_finder_limit,
        "trigger_entity_matcher": True,
        "entity_matcher_limit": entity_matcher_limit,
        "trigger_hint_generator": True,
        "hint_generator_limit": hint_generator_limit,
        "refresh_prioritization": True,
        "write_bigquery": write_bigquery,
    }

    try:
        response = requests.post(
            f"{GD_URL}/run-gap-detector",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=_REQUEST_TIMEOUT,
        )

        if response.status_code == 422:
            log.warning(
                "Gap Detector returned 422 for profile_ids. "
                "Retrying without the field. Full profiles will be queued. "
                "Response body: %.500s",
                response.text,
            )
            return _retry_without_profile_ids(payload)

        response.raise_for_status()
        return _parse_success(response)

    except requests.Timeout:
        return PipelineRunResult(
            success=False,
            error_message=(
                "The Gap Detector did not respond within 90 seconds. "
                "The service may be cold-starting. Wait a moment and try again."
            ),
        )
    except requests.ConnectionError:
        return PipelineRunResult(
            success=False,
            error_message=(
                "Could not connect to the Gap Detector service. "
                "Check that GD_URL is correct and the service is deployed."
            ),
        )
    except requests.HTTPError as exc:
        return PipelineRunResult(
            success=False,
            error_message=f"Gap Detector returned an error: {exc}",
        )
    except requests.RequestException as exc:
        return PipelineRunResult(
            success=False,
            error_message=f"Unexpected error contacting the Gap Detector: {exc}",
        )


def _retry_without_profile_ids(original_payload: dict) -> PipelineRunResult:
    """
    Retry the Gap Detector call with profile_ids stripped from the payload.

    Called only when the first attempt returned 422, which means the
    server-side filtering field is not yet implemented.
    """
    stripped = {k: v for k, v in original_payload.items() if k != "profile_ids"}

    try:
        response = requests.post(
            f"{GD_URL}/run-gap-detector",
            json=stripped,
            headers={"Content-Type": "application/json"},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        result = _parse_success(response)
        result.warning = (
            "The Gap Detector is not yet filtering by profile ID, so all profiles "
            "in the queue are being processed. Your requested IDs will still be "
            "picked up, but the run may take longer than usual."
        )
        return result

    except requests.RequestException as exc:
        return PipelineRunResult(
            success=False,
            error_message=f"Gap Detector retry also failed: {exc}",
        )


def _parse_success(response: requests.Response) -> PipelineRunResult:
    """Extract the run_id from a successful Gap Detector response."""
    try:
        data = response.json()
    except ValueError:
        return PipelineRunResult(
            success=False,
            error_message="Gap Detector returned an unreadable response body.",
        )

    run_id = data.get("gap_detector_run_id")
    if not run_id:
        log.warning("Gap Detector response missing gap_detector_run_id: %s", data)

    return PipelineRunResult(success=True, run_id=run_id)
