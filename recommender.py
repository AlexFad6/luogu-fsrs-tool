"""Recommendation and statistics queries."""

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone


def weak_tags(connection: sqlite3.Connection) -> list[tuple[str, float]]:
    rows = connection.execute(
        """SELECT t.name AS algorithm_tag, r.score
           FROM review_records r
           JOIN problem_tags pt ON pt.pid = r.pid
           JOIN tags t ON t.id = pt.tag_id AND t.category_l1 = '算法'"""
    ).fetchall()
    stats: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        stats[row["algorithm_tag"]].append(row["score"])
    return sorted(
        [(tag, 1 - sum(scores) / len(scores)) for tag, scores in stats.items()
         if len(scores) >= 2 and 1 - sum(scores) / len(scores) > 0.3],
        key=lambda item: item[1], reverse=True,
    )


def new_recommendations(connection: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    weak = [tag for tag, _ in weak_tags(connection)]
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
    reviewed = connection.execute("SELECT COUNT(DISTINCT pid) FROM review_records").fetchone()[0]
    average = connection.execute("SELECT AVG(score) FROM review_records").fetchone()[0] or 0
    rows = connection.execute(
        "SELECT DISTINCT date(review_date) AS day FROM review_records ORDER BY day DESC"
    ).fetchall()
    streak = 0
    expected = datetime.now(timezone.utc).date()
    for row in rows:
        current = date.fromisoformat(row["day"])
        if current == expected:
            streak += 1
            expected -= timedelta(days=1)
        elif current < expected:
            break
    return {"total": total, "reviewed": reviewed, "accuracy": average,
            "weak_tags": weak_tags(connection), "streak": streak}
