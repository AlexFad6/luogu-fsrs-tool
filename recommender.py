"""Recommendation and statistics queries."""

import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone


def _tags(row: sqlite3.Row) -> list[str]:
    try:
        return json.loads(row["tags"] or "[]")
    except json.JSONDecodeError:
        return []


def weak_tags(connection: sqlite3.Connection) -> list[tuple[str, float]]:
    rows = connection.execute(
        "SELECT tags, score FROM review_records r JOIN problems p ON p.pid = r.pid"
    ).fetchall()
    stats: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for tag in _tags(row):
            stats[tag].append(row["score"])
    return sorted(
        [(tag, 1 - sum(scores) / len(scores)) for tag, scores in stats.items()
         if len(scores) > 2 and 1 - sum(scores) / len(scores) > 0.3],
        key=lambda item: item[1], reverse=True,
    )


def new_recommendations(connection: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    weak = [tag for tag, _ in weak_tags(connection)]
    rows = connection.execute(
        "SELECT * FROM problems WHERE is_solved = 0 ORDER BY pid"
    ).fetchall()
    if not weak:
        return list(rows[:limit])
    matching = [row for row in rows if any(tag in weak for tag in _tags(row))]
    return matching[:limit]


def statistics(connection: sqlite3.Connection) -> dict:
    total = connection.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
    reviewed = connection.execute("SELECT COUNT(DISTINCT pid) FROM review_records").fetchone()[0]
    average = connection.execute("SELECT AVG(score) FROM review_records").fetchone()[0] or 0
    rows = connection.execute(
        "SELECT DISTINCT date(review_date) AS day FROM review_records ORDER BY day DESC"
    ).fetchall()
    streak = 0
    expected = date.today()
    for row in rows:
        current = date.fromisoformat(row["day"])
        if current == expected:
            streak += 1
            expected -= timedelta(days=1)
        elif current < expected:
            break
    return {"total": total, "reviewed": reviewed, "accuracy": average,
            "weak_tags": weak_tags(connection), "streak": streak}
