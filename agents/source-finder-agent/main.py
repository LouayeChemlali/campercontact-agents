from __future__ import annotations

# CLI entry point for running the source finder manually against a single profile

import argparse
import json

from source_finder.core import run_source_finder


def parse_args():
    parser = argparse.ArgumentParser(description="Run Source Finder for one flagged Campercontact profile.")
    parser.add_argument("--profile_id", required=True, help="Campercontact id/sitecode flagged by Gap Detector")
    parser.add_argument("--gap_detector_run_id", default="", help="Optional Gap Detector run ID for traceability")
    parser.add_argument("--page_size", type=int, default=10)
    parser.add_argument("--max_candidate_urls", type=int, default=20)
    parser.add_argument("--export_csv", action="store_true")
    parser.add_argument("--no_bigquery", action="store_true", help="Run without writing results to BigQuery")
    return parser.parse_args()


def main():
    """Run source finder for a single profile and print the result as JSON."""
    args = parse_args()
    result = run_source_finder(
        profile_id=args.profile_id,
        gap_detector_run_id=args.gap_detector_run_id,
        page_size=args.page_size,
        max_candidate_urls=args.max_candidate_urls,
        export_csv=args.export_csv,
        write_bigquery=not args.no_bigquery,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
