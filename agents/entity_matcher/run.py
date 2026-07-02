import argparse

# loads source evidence, CC profiles, and gap rows from BQ, runs comparisons, writes results back

from collections import Counter
from typing import Any

from google.cloud import bigquery

from .config import BIGQUERY_PROJECT
from .io_layer import (
    combine_profiles_gap_and_sources,
    load_cc_profiles,
    load_gap_rows_for_profiles,
    load_sources_for_profiles,
    write_output,
)
from .matcher import match_batch


def run_entity_matcher(
    *,
    profile_ids: list[str] | None = None,
    gap_detector_run_id: str | None = None,
    limit: int | None = None,
    write: bool = False,
    append: bool = True,
    use_gap_filter: bool = True,
) -> dict[str, Any]:
    """Run Entity Matcher for Source Finder rows, optionally scoped to one Gap Detector run."""
    client = bigquery.Client(project=BIGQUERY_PROJECT)

    print("Loading Source Finder evidence...")
    source_rows = load_sources_for_profiles(
        client,
        sitecodes=profile_ids,
        gap_detector_run_id=gap_detector_run_id,
        limit=None,
    )
    print(f"Loaded {len(source_rows)} source rows.")

    if not source_rows:
        summary = write_output(client, [], dry_run=not write, truncate=not append)
        return {
            "status": "completed",
            "message": "No Source Finder rows found for this scope.",
            "profiles_loaded": 0,
            "source_rows_loaded": 0,
            "profiles_with_sources": 0,
            "matches_before_filter": 0,
            **summary,
        }

    # pull unique profile IDs from the source rows so we only load profiles we actually have evidence for
    effective_sitecodes = sorted({str(r.get("profile_id") or "").strip() for r in source_rows if r.get("profile_id")})
    if profile_ids:
        requested = {str(p).strip() for p in profile_ids}
        effective_sitecodes = [s for s in effective_sitecodes if s in requested]

    if limit and not profile_ids:
        effective_sitecodes = effective_sitecodes[: int(limit)]

    print("Loading Campercontact profiles...")
    cc_rows = load_cc_profiles(client, sitecodes=effective_sitecodes)
    print(f"Loaded {len(cc_rows)} CC profiles.")

    print("Loading Gap Detector rows...")
    gap_rows_by_sitecode = load_gap_rows_for_profiles(
        client,
        [str(r.get("sitecode") or "") for r in cc_rows],
    )
    print(f"Loaded {len(gap_rows_by_sitecode)} Gap Detector rows.")

    combined = combine_profiles_gap_and_sources(cc_rows, source_rows, gap_rows_by_sitecode)
    profiles_with_sources = [(cc, srcs, gap) for cc, srcs, gap in combined if srcs]
    print(f"Loaded {len(cc_rows)} profiles, {len(profiles_with_sources)} with sources.")

    results = match_batch(profiles_with_sources, use_gap_filter=use_gap_filter)

    status_counts = Counter(r["verification_status"] for r in results)
    print("Status distribution before NO_DATA filter:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    write_summary = write_output(client, results, dry_run=not write, truncate=not append)

    return {
        "status": "completed",
        "gap_detector_run_id": gap_detector_run_id or "",
        "profile_ids": profile_ids or [],
        "profiles_loaded": len(cc_rows),
        "source_rows_loaded": len(source_rows),
        "gap_rows_loaded": len(gap_rows_by_sitecode),
        "profiles_with_sources": len(profiles_with_sources),
        "matches_before_filter": len(results),
        "status_counts_before_filter": dict(status_counts),
        **write_summary,
    }


def main():
    """CLI entry point for running the entity matcher against BigQuery data."""
    parser = argparse.ArgumentParser(description="Entity Matcher: compares CC profiles against external sources")
    parser.add_argument("--limit", type=int, default=None, help="Max number of profiles to process after source loading")
    parser.add_argument("--write", action="store_true", default=False, help="Write results to BigQuery (default: dry-run)")
    parser.add_argument("--append", action="store_true", default=False, help="Append instead of replacing the output table (default: truncate on write)")
    parser.add_argument("--profile-ids", type=str, default=None, help="Comma-separated sitecodes, e.g. 15788,160668")
    parser.add_argument("--gap-detector-run-id", type=str, default=None, help="Only match Source Finder rows from this Gap Detector run")
    parser.add_argument("--all-fields", action="store_true", default=False, help="Compare all active fields instead of filtering by Gap Detector recommended_actions")
    args = parser.parse_args()

    profile_ids = None
    if args.profile_ids:
        profile_ids = [s.strip() for s in args.profile_ids.split(",") if s.strip()]

    run_entity_matcher(
        profile_ids=profile_ids,
        gap_detector_run_id=args.gap_detector_run_id,
        limit=args.limit,
        write=args.write,
        append=args.append,
        use_gap_filter=not args.all_fields,
    )


if __name__ == "__main__":
    main()
