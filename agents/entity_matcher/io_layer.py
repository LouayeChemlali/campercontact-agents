# handles all BigQuery reads and writes for the entity matcher, profiles, sources, gap rows, and results

import json
from collections import Counter
from typing import Any

from google.cloud import bigquery

from .config import (
    BIGQUERY_PROJECT,
    CC_TABLE,
    GAP_TABLE,
    OUTPUT_TABLE,
    SOURCE_SUCCESS_STATUSES,
    SOURCE_TABLE,
)
from .comparators import NO_DATA


def _fully_qualified_table(table_name: str) -> str:
    """Return a safely quoted BigQuery table reference."""
    if table_name.count(".") == 2:
        return f"`{table_name}`"
    return f"`{BIGQUERY_PROJECT}.{table_name}`"


def load_cc_profiles(
    client: bigquery.Client,
    sitecodes: list[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Load CC profiles from BigQuery, optionally filtered to a list of sitecodes."""
    table = _fully_qualified_table(CC_TABLE)
    query_parameters: list[bigquery.ScalarQueryParameter | bigquery.ArrayQueryParameter] = []

    where_parts = []
    if sitecodes:
        where_parts.append("CAST(sitecode AS STRING) IN UNNEST(@sitecodes)")
        query_parameters.append(
            bigquery.ArrayQueryParameter("sitecodes", "STRING", [str(s) for s in sitecodes])
        )

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    limit_clause = "LIMIT @limit" if limit else ""
    if limit:
        query_parameters.append(bigquery.ScalarQueryParameter("limit", "INT64", int(limit)))

    query = f"SELECT * FROM {table} {where_clause} {limit_clause}"
    job_config = bigquery.QueryJobConfig(query_parameters=query_parameters)
    rows = list(client.query(query, job_config=job_config).result())
    return [dict(row.items()) for row in rows]


def load_sources_for_profiles(
    client: bigquery.Client,
    sitecodes: list[str] | None = None,
    gap_detector_run_id: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Load Source Finder evidence rows, optionally scoped to a Gap Detector run/profile list."""
    table = _fully_qualified_table(SOURCE_TABLE)
    query_parameters: list[bigquery.ScalarQueryParameter | bigquery.ArrayQueryParameter] = []
    where_parts = []

    if SOURCE_SUCCESS_STATUSES:
        where_parts.append("CAST(extraction_status AS STRING) IN UNNEST(@statuses)")
        query_parameters.append(
            bigquery.ArrayQueryParameter("statuses", "STRING", SOURCE_SUCCESS_STATUSES)
        )

    if sitecodes:
        where_parts.append("CAST(profile_id AS STRING) IN UNNEST(@sitecodes)")
        query_parameters.append(
            bigquery.ArrayQueryParameter("sitecodes", "STRING", [str(s) for s in sitecodes])
        )

    if gap_detector_run_id:
        where_parts.append("CAST(gap_detector_run_id AS STRING) = @gap_detector_run_id")
        query_parameters.append(
            bigquery.ScalarQueryParameter("gap_detector_run_id", "STRING", gap_detector_run_id)
        )

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    limit_clause = "LIMIT @limit" if limit else ""
    if limit:
        query_parameters.append(bigquery.ScalarQueryParameter("limit", "INT64", int(limit)))

    query = f"""
        SELECT *
        FROM {table}
        {where_clause}
        ORDER BY created_at DESC
        {limit_clause}
    """
    job_config = bigquery.QueryJobConfig(query_parameters=query_parameters)
    rows = list(client.query(query, job_config=job_config).result())
    return _dedup_sources([dict(row.items()) for row in rows])


def load_gap_rows_for_profiles(
    client: bigquery.Client,
    sitecodes: list[str],
) -> dict[str, dict]:
    """Load Gap Detector rows for the profiles being matched.

    The Gap Detector output table does not need to contain the generated Cloud Run
    gap_detector_run_id. The run id is carried through the queue and Source Finder
    output, while this table gives us the original gap/recommended action context.
    """
    if not sitecodes:
        return {}

    table = _fully_qualified_table(GAP_TABLE)
    query = f"""
        SELECT *
        FROM {table}
        WHERE CAST(sitecode AS STRING) IN UNNEST(@sitecodes)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("sitecodes", "STRING", [str(s) for s in sitecodes])
        ]
    )
    rows = [dict(row.items()) for row in client.query(query, job_config=job_config).result()]

    by_sitecode: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("sitecode") or "").strip()
        if key and key not in by_sitecode:
            by_sitecode[key] = row
    return by_sitecode


def _dedup_sources(source_rows: list[dict]) -> list[dict]:
    """Keep one row per (profile_id, source_url), preferring rows linked to a Gap Detector run."""
    seen: dict[tuple, dict] = {}
    for row in source_rows:
        key = (str(row.get("profile_id") or ""), str(row.get("source_url") or ""))
        if key not in seen or (not seen[key].get("gap_detector_run_id") and row.get("gap_detector_run_id")):
            seen[key] = row
    return list(seen.values())


def combine_profiles_gap_and_sources(
    cc_rows: list[dict],
    source_rows: list[dict],
    gap_rows_by_sitecode: dict[str, dict] | None = None,
) -> list[tuple[dict, list[dict], dict | None]]:
    """Join in Python on sitecode (CC/gap) == profile_id (source), all as strings."""
    gap_rows_by_sitecode = gap_rows_by_sitecode or {}

    source_by_id: dict[str, list[dict]] = {}
    for row in source_rows:
        key = str(row.get("profile_id") or "").strip()
        source_by_id.setdefault(key, []).append(row)

    result = []
    for cc in cc_rows:
        key = str(cc.get("sitecode") or "").strip()
        sources = source_by_id.get(key, [])
        gap_row = gap_rows_by_sitecode.get(key)
        result.append((cc, sources, gap_row))
    return result


# Backwards-compatible wrapper for older imports.
def combine_profiles_with_sources(
    cc_rows: list[dict],
    source_rows: list[dict],
) -> list[tuple[dict, list[dict]]]:
    combined = combine_profiles_gap_and_sources(cc_rows, source_rows, {})
    return [(cc, sources) for cc, sources, _gap in combined]


def write_output(
    client: bigquery.Client,
    results: list[dict],
    dry_run: bool = True,
    truncate: bool = False,
) -> dict[str, Any]:
    """Write entity matcher results to BigQuery, filtering out NO_DATA rows first."""
    # Filter out NO_DATA rows before writing.
    filtered = [r for r in results if r["verification_status"] != NO_DATA]

    status_counts = Counter(r["verification_status"] for r in filtered)
    total = len(filtered)
    skipped = len(results) - total

    summary = {
        "rows_before_filter": len(results),
        "rows_written_or_ready": total,
        "no_data_rows_filtered": skipped,
        "status_counts": dict(status_counts),
        "output_table": f"{BIGQUERY_PROJECT}.{OUTPUT_TABLE}",
        "dry_run": dry_run,
        "truncate": truncate,
    }

    if dry_run:
        print(f"\n[DRY-RUN] {total} rows to write, {skipped} NO_DATA filtered out.")
        print("\nStatus distribution:")
        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count}")
        print("\nFirst 10 rows:")
        for row in filtered[:10]:
            print(json.dumps(row, ensure_ascii=False, indent=2, default=str))
        return summary

    if not filtered:
        print("No rows to write after NO_DATA filter.")
        return summary

    table_ref = f"{BIGQUERY_PROJECT}.{OUTPUT_TABLE}"
    disposition = bigquery.WriteDisposition.WRITE_TRUNCATE if truncate else bigquery.WriteDisposition.WRITE_APPEND
    schema = [
        bigquery.SchemaField("profile_id", "STRING"),
        bigquery.SchemaField("profile_name", "STRING"),
        bigquery.SchemaField("field_name", "STRING"),
        bigquery.SchemaField("current_value", "STRING"),
        bigquery.SchemaField("external_value", "STRING"),
        bigquery.SchemaField("source_url", "STRING"),
        bigquery.SchemaField("source_domain", "STRING"),
        bigquery.SchemaField("matched_source_url", "STRING"),
        bigquery.SchemaField("matched_source_domain", "STRING"),
        bigquery.SchemaField("matched_field", "STRING"),
        bigquery.SchemaField("current_campercontact_value", "STRING"),
        bigquery.SchemaField("external_source_value", "STRING"),
        bigquery.SchemaField("source_title", "STRING"),
        bigquery.SchemaField("source_snippet", "STRING"),
        bigquery.SchemaField("entity_match_score", "FLOAT"),
        bigquery.SchemaField("verification_status", "STRING"),
        bigquery.SchemaField("comparison_type", "STRING"),
        bigquery.SchemaField("num_sources", "INTEGER"),
        bigquery.SchemaField("source_finder_run_id", "STRING"),
        bigquery.SchemaField("gap_detector_run_id", "STRING"),
        bigquery.SchemaField("gap_recommended_actions", "STRING"),
        bigquery.SchemaField("run_timestamp", "TIMESTAMP"),
    ]

    job_config = bigquery.LoadJobConfig(
        write_disposition=disposition,
        schema=schema,
        ignore_unknown_values=True,
    )
    # FORCE_PROFILE_ID_STRING_FIX
    # BigQuery table expects profile_id as STRING. Without this, JSON loading can
    # infer numeric-looking IDs like 19060 as INTEGER and fail with schema mismatch.
    for row in filtered:
        if "profile_id" in row and row["profile_id"] is not None:
            row["profile_id"] = str(row["profile_id"])
        if "sitecode" in row and row["sitecode"] is not None:
            row["sitecode"] = str(row["sitecode"])
        if "gap_detector_run_id" in row and row["gap_detector_run_id"] is not None:
            row["gap_detector_run_id"] = str(row["gap_detector_run_id"])

    load_job = client.load_table_from_json(filtered, table_ref, job_config=job_config)
    load_job.result()

    print(f"Written: {total} rows to {table_ref}")
    print("\nStatus distribution:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    return summary
