from __future__ import annotations

# pulls pending profiles from the source_finder_queue table and runs the source finder on each one

import datetime
from typing import Any, Dict, List

from google.cloud import bigquery

from .config import PROJECT_ID, QUEUE_DATASET, QUEUE_TABLE
from .core import run_source_finder


def _queue_table_id() -> str:
    return f"{PROJECT_ID}.{QUEUE_DATASET}.{QUEUE_TABLE}"


def fetch_pending_queue_items(
    limit: int = 5,
    gap_detector_run_id: str | None = None,
) -> List[Dict[str, Any]]:
    """Pull pending profiles from the source finder queue, ordered by priority."""
    client = bigquery.Client(project=PROJECT_ID)
    table_id = _queue_table_id()

    filters = [
        "status = 'pending'",
        "attempts < 3",
    ]

    query_parameters = [
        bigquery.ScalarQueryParameter("limit", "INT64", limit)
    ]

    if gap_detector_run_id:
        filters.append("gap_detector_run_id = @gap_detector_run_id")
        query_parameters.append(
            bigquery.ScalarQueryParameter(
                "gap_detector_run_id",
                "STRING",
                gap_detector_run_id,
            )
        )

    where_clause = " AND ".join(filters)

    query = f"""
        SELECT
          queue_id,
          gap_detector_run_id,
          sitecode,
          priority,
          attempts
        FROM `{table_id}`
        WHERE {where_clause}
        ORDER BY priority DESC, created_at ASC
        LIMIT @limit
    """

    job_config = bigquery.QueryJobConfig(query_parameters=query_parameters)

    rows = client.query(query, job_config=job_config).result()
    return [dict(row.items()) for row in rows]


def mark_processing(queue_id: str) -> None:
    """Set a queue item to 'processing' and increment its attempt counter."""
    client = bigquery.Client(project=PROJECT_ID)
    table_id = _queue_table_id()

    query = f"""
        UPDATE `{table_id}`
        SET
          status = 'processing',
          attempts = IFNULL(attempts, 0) + 1,
          updated_at = CURRENT_TIMESTAMP()
        WHERE queue_id = @queue_id
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("queue_id", "STRING", queue_id)
        ]
    )

    client.query(query, job_config=job_config).result()


def mark_completed(queue_id: str) -> None:
    """Mark a queue item as completed after a successful run."""
    client = bigquery.Client(project=PROJECT_ID)
    table_id = _queue_table_id()

    query = f"""
        UPDATE `{table_id}`
        SET
          status = 'completed',
          updated_at = CURRENT_TIMESTAMP(),
          processed_at = CURRENT_TIMESTAMP(),
          last_error = NULL
        WHERE queue_id = @queue_id
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("queue_id", "STRING", queue_id)
        ]
    )

    client.query(query, job_config=job_config).result()


def mark_failed(queue_id: str, error: str) -> None:
    """Mark a queue item as failed and store the error message."""
    client = bigquery.Client(project=PROJECT_ID)
    table_id = _queue_table_id()

    query = f"""
        UPDATE `{table_id}`
        SET
          status = 'failed',
          updated_at = CURRENT_TIMESTAMP(),
          last_error = @error
        WHERE queue_id = @queue_id
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("queue_id", "STRING", queue_id),
            bigquery.ScalarQueryParameter("error", "STRING", str(error)[:2000]),
        ]
    )

    client.query(query, job_config=job_config).result()


def run_pending_source_finder(
    limit: int = 5,
    write_bigquery: bool = True,
    gap_detector_run_id: str | None = None,
) -> Dict[str, Any]:
    requested_gap_detector_run_id = gap_detector_run_id
    items = fetch_pending_queue_items(
        limit=limit,
        gap_detector_run_id=requested_gap_detector_run_id,
    )

    results = []

    for item in items:
        queue_id = item["queue_id"]
        sitecode = str(item["sitecode"])
        item_gap_detector_run_id = str(item.get("gap_detector_run_id") or "")

        try:
            mark_processing(queue_id)

            result = run_source_finder(
                profile_id=sitecode,
                gap_detector_run_id=item_gap_detector_run_id,
                page_size=10,
                max_candidate_urls=20,
                export_csv=False,
                write_bigquery=write_bigquery,
            )

            mark_completed(queue_id)

            results.append({
                "queue_id": queue_id,
                "sitecode": sitecode,
                "status": "completed",
                "result": result,
            })

        except Exception as exc:
            mark_failed(queue_id, str(exc))

            results.append({
                "queue_id": queue_id,
                "sitecode": sitecode,
                "status": "failed",
                "error": str(exc),
            })

    return {
        "checked_at": datetime.datetime.utcnow().isoformat(),
        "gap_detector_run_id_filter": requested_gap_detector_run_id,
        "items_found": len(items),
        "items_processed": len(results),
        "results": results,
    }
