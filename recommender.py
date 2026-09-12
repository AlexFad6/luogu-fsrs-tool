"""Recommendation and statistics queries."""

import sqlite3
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from tag_stats import weakness_stats
import db


def weak_tags(connection: sqlite3.Connection) -> list[tuple[str, float]]:
    rows = []
    for attempt in db.get_all_attempts_for_stats(connection):
        try:
            tags = json.loads(attempt.get("algorithm_tags") or "[]")
        except (TypeError, ValueError):
            tags = []
        rows.extend(({"algorithm_tag": tag, "score": attempt["score"]} for tag in tags))
    stats: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        stats[row["algorithm_tag"]].append(row["score"])
    return sorted(
        [(tag, 1 - sum(scores) / len(scores)) for tag, scores in stats.items()
         if len(scores) >= 2 and 1 - sum(scores) / len(scores) > 0.3],
        key=lambda item: item[1], reverse=True,
    )


def new_recommendations(connection: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    weak = [item["tag"] for item in weakness_stats(connection)
            if item["weakness"] is not None and item["weakness"] > 0]
    if not weak:
        return list(connection.execute(
            "SELECT * FROM problems WHERE is_solved = 0 ORDER BY pid LIMIT ?", (limit,)
        ))
    placeholders = ",".join("?" for _ in weak)
    return list(connection.execute(
        f"""SELECT DISTINCT p.* FROM problems p
            JOIN problem_tags pt ON pt.pid = p.pid
            JOIN tags t ON t.id = pt.tag_id
            WHERE p.is_solved = 0 AND t.category_l1 = '算法'
              AND t.name IN ({placeholders})
            ORDER BY p.pid LIMIT ?""",
        (*weak, limit),
    ))


def statistics(connection: sqlite3.Connection) -> dict:
    total = connection.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
    attempts = db.get_all_attempts_for_stats(connection)
    reviewed = len({row["pid"] for row in attempts})
    average = (sum(row["score"] for row in attempts) / len(attempts)) if attempts else 0
    days = sorted({row["review_date"][:10] for row in attempts}, reverse=True)
    streak = 0
    expected = datetime.now(timezone.utc).date()
    for day in days:
        current = date.fromisoformat(day)
        if current == expected:
            streak += 1
            expected -= timedelta(days=1)
        elif current < expected:
            break
    return {"total": total, "reviewed": reviewed, "accuracy": average,
            "weak_tags": weak_tags(connection), "streak": streak}
