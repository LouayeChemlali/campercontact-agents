import json
from collections import Counter

from google.cloud import bigquery

from .config import CC_TABLE, SOURCE_TABLE, OUTPUT_TABLE, BIGQUERY_PROJECT
from .comparators import NO_DATA


def load_cc_profiles(
    client: bigquery.Client,
    sitecodes: list[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    table = f"`{BIGQUERY_PROJECT}.{CC_TABLE}`"
    where_clause = ""
    if sitecodes:
        ids = ", ".join(f"'{s}'" for s in sitecodes)
        where_clause = f"WHERE CAST(sitecode AS STRING) IN ({ids})"
    limit_clause = f"LIMIT {limit}" if limit else ""
    query = f"SELECT * FROM {table} {where_clause} {limit_clause}"
    rows = list(client.query(query).result())
    return [dict(row) for row in rows]


def load_sources_for_profiles(
    client: bigquery.Client,
    sitecodes: list[str],
    gap_detector_run_id: str | None = None,
) -> list[dict]:
    # Load all success rows — filtering by sitecodes via a large IN clause breaks
    # for full runs (68k+ values). Python join in combine_profiles_with_sources handles matching.
    # When gap_detector_run_id is provided, scope to that run only to avoid matching stale data.
    dataset, tbl = SOURCE_TABLE.split(".", 1)
    table = f"`{BIGQUERY_PROJECT}.{dataset}.{tbl}`"
    if gap_detector_run_id:
        try:
            params = [bigquery.ScalarQueryParameter("run_id", "STRING", gap_detector_run_id)]
            query = f"SELECT * FROM {table} WHERE extraction_status = 'success' AND gap_detector_run_id = @run_id"
            rows = list(client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result())
            return _dedup_sources([dict(row) for row in rows])
        except Exception:
            # Column may not exist in older table schema — fall through to load all sources.
            pass
    query = f"SELECT * FROM {table} WHERE extraction_status = 'success'"
    rows = list(client.query(query).result())
    return _dedup_sources([dict(row) for row in rows])


def _dedup_sources(source_rows: list[dict]) -> list[dict]:
    """Keep one row per (profile_id, source_url), preferring rows with a gap_detector_run_id."""
    seen: dict[tuple, dict] = {}
    for row in source_rows:
        key = (str(row.get("profile_id") or ""), str(row.get("source_url") or ""))
        if key not in seen or (not seen[key].get("gap_detector_run_id") and row.get("gap_detector_run_id")):
            seen[key] = row
    return list(seen.values())


def combine_profiles_with_sources(
    cc_rows: list[dict],
    source_rows: list[dict],
) -> list[tuple[dict, list[dict]]]:
    """Join in Python on sitecode (CC) == profile_id (source), both as strings."""
    source_by_id: dict[str, list[dict]] = {}
    for row in source_rows:
        key = str(row.get("profile_id") or "").strip()
        source_by_id.setdefault(key, []).append(row)

    result = []
    for cc in cc_rows:
        key = str(cc.get("sitecode") or "").strip()
        sources = source_by_id.get(key, [])
        result.append((cc, sources))
    return result


def write_output(
    client: bigquery.Client,
    results: list[dict],
    dry_run: bool = True,
    truncate: bool = False,
) -> None:
    # Filter out NO_DATA rows before writing
    filtered = [r for r in results if r["verification_status"] != NO_DATA]

    status_counts = Counter(r["verification_status"] for r in filtered)
    total = len(filtered)
    skipped = len(results) - total

    if dry_run:
        print(f"\n[DRY-RUN] {total} rows to write, {skipped} NO_DATA filtered out.")
        print("\nStatus distribution:")
        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count}")
        print("\nFirst 10 rows:")
        for row in filtered[:10]:
            print(json.dumps(row, ensure_ascii=False, indent=2))
        return

    if not filtered:
        print("No rows to write after NO_DATA filter.")
        return

    table_ref = f"{BIGQUERY_PROJECT}.{OUTPUT_TABLE}"
    disposition = bigquery.WriteDisposition.WRITE_TRUNCATE if truncate else bigquery.WriteDisposition.WRITE_APPEND
    job_config = bigquery.LoadJobConfig(
        write_disposition=disposition,
        autodetect=True,
    )
    load_job = client.load_table_from_json(filtered, table_ref, job_config=job_config)
    load_job.result()

    print(f"Written: {total} rows to {table_ref}")
    print("\nStatus distribution:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")
