"""User-only primary-tag and timing weakness statistics."""

from __future__ import annotations

import math
import sqlite3
from statistics import median
from pathlib import Path

import yaml


def _config() -> dict:
    with Path(__file__).with_name("config.yaml").open(encoding="utf-8") as stream:
        return (yaml.safe_load(stream) or {}).get("recommend", {})


def weakness_stats(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """SELECT r.primary_tag, p.difficulty, r.duration
           FROM review_records r JOIN problems p ON p.pid = r.pid
           WHERE r.primary_tag IS NOT NULL AND r.duration IS NOT NULL"""
    ).fetchall()
    by_difficulty: dict[str, list[float]] = {}
    for row in rows:
        floor = configured_floor(row["difficulty"])
        by_difficulty.setdefault(row["difficulty"], []).append(max(row["duration"], floor))
    baselines = {key: median(values) for key, values in by_difficulty.items() if len(values) >= 3}
    residuals: dict[str, list[float]] = {}
    for row in rows:
        if row["difficulty"] in baselines:
            residuals.setdefault(row["primary_tag"], []).append(
                math.log2(max(row["duration"], configured_floor(row["difficulty"]))
                          / baselines[row["difficulty"]])
            )
    minimum = _config().get("min_samples_for_weakness", 5)
    return [
        {"tag": tag, "weakness": median(values) if len(values) >= minimum else None,
         "samples": len(values)}
        for tag, values in residuals.items()
    ]
