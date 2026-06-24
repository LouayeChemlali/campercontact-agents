"""HTTP client for the Gap Detector Cloud Run service."""

import logging
from dataclasses import dataclass

import requests
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from config import GD_AUTH, GD_URL, PIPELINE_REQUEST_TIMEOUT_SECONDS

log = logging.getLogger(__name__)


@dataclass
class PipelineRunResult:
    """Structured result returned to the caller after a trigger attempt."""

    success: bool
    run_id: str | None = None
    error_message: str | None = None
    warning: str | None = None
    pipeline_data: dict | None = None


def trigger_pipeline(
    profile_ids: list[str],
    *,
    write_bigquery: bool = True,
) -> PipelineRunResult:
    """Start the full backend pipeline for the given profile IDs."""
    if not GD_URL:
        return PipelineRunResult(
            success=False,
            error_message=(
                "Pipeline trigger is not configured. Set GD_URL in the .env file "
                "or Cloud Run environment variables."
            ),
        )

    n = max(1, len(profile_ids))
    payload: dict = {
        "profile_ids": [str(pid) for pid in profile_ids],
        "trigger_source_finder": True,
        "source_finder_limit": n,
        "trigger_entity_matcher": True,
        "entity_matcher_limit": n * 5,
        "trigger_hint_generator": True,
        "hint_generator_limit": n * 5,
        "trigger_confidence_agent": True,
        "confidence_agent_limit": n * 5,
        "refresh_prioritization": True,
        "write_bigquery": write_bigquery,
    }

    try:
        response = requests.post(
            f"{GD_URL}/run-gap-detector",
            json=payload,
            headers=_headers(),
            timeout=PIPELINE_REQUEST_TIMEOUT_SECONDS,
        )

        if response.status_code == 422:
            log.warning(
                "Gap Detector returned 422 for profile_ids. Retrying without profile_ids. "
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
                f"The Gap Detector did not respond within "
                f"{PIPELINE_REQUEST_TIMEOUT_SECONDS} seconds. The service may be cold-starting "
                "or the pipeline chain may still be running."
            ),
        )
    except requests.ConnectionError:
        return PipelineRunResult(
            success=False,
            error_message=(
                "Could not connect to the Gap Detector service. Check that GD_URL is correct "
                "and the Cloud Run service is deployed."
            ),
        )
    except requests.HTTPError as exc:
        body = getattr(exc.response, "text", "") if exc.response is not None else ""
        return PipelineRunResult(
            success=False,
            error_message=f"Gap Detector returned an error: {exc}. {body[:500]}",
        )
    except requests.RequestException as exc:
        return PipelineRunResult(
            success=False,
            error_message=f"Unexpected error contacting the Gap Detector: {exc}",
        )
    except Exception as exc:
        log.exception("Unexpected pipeline trigger failure")
        return PipelineRunResult(
            success=False,
            error_message=f"Unexpected pipeline trigger failure: {exc}",
        )


def _headers() -> dict[str, str]:
    """Return headers for Cloud Run. Adds ID token when GD_AUTH=true."""
    headers = {"Content-Type": "application/json"}
    if GD_AUTH:
        token = id_token.fetch_id_token(Request(), GD_URL)
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _retry_without_profile_ids(original_payload: dict) -> PipelineRunResult:
    """Retry the Gap Detector call with profile_ids stripped from the payload."""
    stripped = {k: v for k, v in original_payload.items() if k != "profile_ids"}

    try:
        response = requests.post(
            f"{GD_URL}/run-gap-detector",
            json=stripped,
            headers=_headers(),
            timeout=PIPELINE_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        result = _parse_success(response)
        result.warning = (
            "The Gap Detector did not accept profile_ids, so the retry was sent without "
            "that filter. The run may process more profiles than requested."
        )
        return result

    except requests.RequestException as exc:
        return PipelineRunResult(
            success=False,
            error_message=f"Gap Detector retry also failed: {exc}",
        )


def _parse_success(response: requests.Response) -> PipelineRunResult:
    """Extract the run_id and pipeline stats from a successful Gap Detector response."""
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

    return PipelineRunResult(success=True, run_id=run_id, pipeline_data=data)
