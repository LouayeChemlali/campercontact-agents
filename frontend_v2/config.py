"""Application configuration loaded from .env at startup."""

import os

from dotenv import load_dotenv

load_dotenv()

BIGQUERY_PROJECT: str = os.environ.get(
    "BIGQUERY_PROJECT", "project-62cd3637-0b98-4aa5-8d5"
)

# Base URL of the deployed Gap Detector Cloud Run service, no trailing slash.
GD_URL: str = os.environ.get("GD_URL", "").rstrip("/")

# Private Cloud Run endpoints need a Google-signed ID token. Keep this true for GCP.
GD_AUTH: bool = os.environ.get("GD_AUTH", "true").lower() in {"1", "true", "yes", "y"}

FLASK_SECRET_KEY: str = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-in-production")

POLLING_INTERVAL_SECONDS: int = int(os.environ.get("POLLING_INTERVAL_SECONDS", "5"))
POLLING_TIMEOUT_SECONDS: int = int(os.environ.get("POLLING_TIMEOUT_SECONDS", "600"))
PIPELINE_REQUEST_TIMEOUT_SECONDS: int = int(os.environ.get("PIPELINE_REQUEST_TIMEOUT_SECONDS", "300"))

BQ_DATASET = "primary_dataset"

# Final backend output table used by the frontend.
BQ_CONFIDENCE_TABLE = f"{BIGQUERY_PROJECT}.{BQ_DATASET}.hint_confidence_results"

# Final anomaly / prioritization queue table.
BQ_QUEUE_TABLE = (
    f"{BIGQUERY_PROJECT}.hint_prioritization.moderator_hint_queue_with_anomaly_v1"
)
