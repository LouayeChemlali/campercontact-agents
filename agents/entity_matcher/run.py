import argparse
from collections import Counter

from google.cloud import bigquery

from .config import BIGQUERY_PROJECT
from .io_layer import load_cc_profiles, load_sources_for_profiles, combine_profiles_with_sources, write_output
from .matcher import match_batch


def main():
    parser = argparse.ArgumentParser(description="Entity Matcher — compares CC profiles against external sources")
    parser.add_argument("--limit", type=int, default=None, help="Max number of profiles to load (default: all)")
    parser.add_argument("--write", action="store_true", default=False, help="Write results to BigQuery (default: dry-run)")
    parser.add_argument("--append", action="store_true", default=False, help="Append instead of replacing the output table (default: truncate on write)")
    parser.add_argument("--profile-ids", type=str, default=None, help="Comma-separated sitecodes, e.g. 15788,160668")
    args = parser.parse_args()

    sitecodes = None
    if args.profile_ids:
        sitecodes = [s.strip() for s in args.profile_ids.split(",") if s.strip()]

    client = bigquery.Client(project=BIGQUERY_PROJECT)

    print("Loading CC profiles...")
    cc_rows = load_cc_profiles(client, sitecodes=sitecodes, limit=None if sitecodes else args.limit)
    print(f"Loaded {len(cc_rows)} CC profiles.")

    if not cc_rows:
        print("No profiles found. Exiting.")
        return

    effective_sitecodes = sitecodes or [str(r.get("sitecode") or "") for r in cc_rows]

    print("Loading sources (Source Finder)...")
    source_rows = load_sources_for_profiles(client, effective_sitecodes)
    print(f"Loaded {len(source_rows)} source rows.")

    combined = combine_profiles_with_sources(cc_rows, source_rows)
    profiles_with_sources = [(cc, srcs) for cc, srcs in combined if srcs]
    print(f"\nLoaded {len(cc_rows)} profiles, {len(profiles_with_sources)} with sources.\n")

    results = match_batch(profiles_with_sources)

    status_counts = Counter(r["verification_status"] for r in results)
    print("Status distribution (before NO_DATA filter):")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    write_output(client, results, dry_run=not args.write, truncate=not args.append)


if __name__ == "__main__":
    main()
