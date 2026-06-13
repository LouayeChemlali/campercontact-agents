from __future__ import annotations

import csv
import os
from typing import Any, Dict, List

from .config import CSV_EXPORT_DIR


def export_rows_csv(rows: List[Dict[str, Any]], profile_id: str, run_id: str) -> str:
    os.makedirs(CSV_EXPORT_DIR, exist_ok=True)
    path = os.path.join(CSV_EXPORT_DIR, f"source_finder_{profile_id}_{run_id}.csv")
    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("")
        return path

    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path
