from __future__ import annotations

from typing import Any, Dict, List

from google.cloud import bigquery

from .config import OUTPUT_DATASET, OUTPUT_TABLE, PROJECT_ID


def insert_rows_bigquery(rows: List[Dict[str, Any]]) -> str:
    table_id = f"{PROJECT_ID}.{OUTPUT_DATASET}.{OUTPUT_TABLE}"

    if not rows:
        return table_id

    client = bigquery.Client(project=PROJECT_ID)
    errors = client.insert_rows_json(table_id, rows)

    if errors:
        raise RuntimeError(f"BigQuery insert errors: {errors}")

    return table_id
