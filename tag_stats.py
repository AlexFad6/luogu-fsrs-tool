"""User-only primary-tag and timing weakness statistics."""

from __future__ import annotations

import math
import sqlite3
from statistics import median
from pathlib import Path
import yaml
import db
from review_scoring import global_floor, pool_baseline


def _config() -> dict:
    with Path(__file__).with_name("config.yaml").open(encoding="utf-8") as stream:
        return (yaml.safe_load(stream) or {}).get("recommend", {})


def weakness_stats(connection: sqlite3.Connection) -> list[dict]:
    rows = [row for row in db.get_all_attempts_for_stats(connection)
            if row.get("primary_tag") is not None and row.get("duration") is not None]
    all_rows = db.get_all_attempts_for_stats(connection)
    baselines = {}
    for difficulty in {row.get("difficulty") for row in all_rows}:
        baselines[difficulty] = pool_baseline(all_rows, difficulty)
    residuals: dict[str, list[float]] = {}
    for row in rows:
        if row["difficulty"] in baselines:
            residuals.setdefault(row["primary_tag"], []).append(
                math.log2(float(row["duration"]) / baselines[row["difficulty"]])
            )
    minimum = _config().get("min_samples_for_weakness", 5)
    return [
        {"tag": tag, "weakness": median(values) if len(values) >= minimum else None,
         "samples": len(values)}
        for tag, values in residuals.items()
    ]
