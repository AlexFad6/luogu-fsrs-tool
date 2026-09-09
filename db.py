"""SQLite persistence for the review tool."""

import json
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from tag_manager import TagManager


if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "luogu_fsrs.db"
BACKUP_DIR = DATA_DIR / "backup"


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a configured database connection."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    """Create all tables when they do not already exist."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS problems (
            pid TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            difficulty TEXT,
            tags TEXT,
            algorithm_tags TEXT,
            technical_tags TEXT,
            all_tags TEXT,
            is_solved INTEGER NOT NULL DEFAULT 0,
            source_url TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS review_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pid TEXT NOT NULL REFERENCES problems(pid),
            review_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            score REAL NOT NULL CHECK(score >= 0 AND score <= 1),
            duration INTEGER,
            note TEXT,
            wrong_submissions INTEGER NOT NULL DEFAULT 0,
            saw_solution INTEGER NOT NULL DEFAULT 0,
            primary_tag TEXT
        );
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            category_l1 TEXT NOT NULL,
            category_l2 TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS problem_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pid TEXT NOT NULL REFERENCES problems(pid) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            UNIQUE(pid, tag_id)
        );
        CREATE INDEX IF NOT EXISTS idx_problem_tags_pid ON problem_tags(pid);
        CREATE INDEX IF NOT EXISTS idx_problem_tags_tag_id ON problem_tags(tag_id);
        CREATE TABLE IF NOT EXISTS card_states (
            pid TEXT PRIMARY KEY REFERENCES problems(pid),
            stability REAL,
            difficulty REAL,
            due_date TEXT,
            last_review TEXT,
            reps INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_card_due ON card_states(due_date);
        CREATE INDEX IF NOT EXISTS idx_review_pid ON review_records(pid);
        """
    )
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(problems)")}
    for name, definition in (
        ("algorithm_tags", "TEXT"), ("technical_tags", "TEXT"), ("all_tags", "TEXT"),
        ("source_url", "TEXT"), ("updated_at", "TEXT"),
    ):
        if name not in columns:
            connection.execute(f"ALTER TABLE problems ADD COLUMN {name} {definition}")
    connection.execute(
        "UPDATE problems SET all_tags = COALESCE(all_tags, tags, '[]'), "
        "algorithm_tags = COALESCE(algorithm_tags, tags, '[]'), "
        "technical_tags = COALESCE(technical_tags, '[]'), "
        "updated_at = COALESCE(updated_at, created_at)"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_algorithm_tags ON problems(algorithm_tags)")
    review_columns = {row["name"] for row in connection.execute("PRAGMA table_info(review_records)")}
    for name, definition in (
        ("wrong_submissions", "INTEGER NOT NULL DEFAULT 0"),
        ("saw_solution", "INTEGER NOT NULL DEFAULT 0"),
        ("primary_tag", "TEXT"),
    ):
        if name not in review_columns:
            connection.execute(f"ALTER TABLE review_records ADD COLUMN {name} {definition}")
    manager = TagManager(connection)
    manager.initialize()
    for row in connection.execute("SELECT pid, all_tags, tags FROM problems"):
        try:
            names = json.loads(row["all_tags"] or row["tags"] or "[]")
        except json.JSONDecodeError:
            names = []
        for name in names:
            tag = manager.get_or_create_tag(str(name))
            connection.execute(
                "INSERT OR IGNORE INTO problem_tags(pid, tag_id) VALUES (?, ?)",
                (row["pid"], tag.id),
            )
    _backfill_primary_tags(connection)
    connection.commit()


def _backfill_primary_tags(connection: sqlite3.Connection) -> None:
    """Backfill primary tags using the least-practiced algorithm tag."""
    rows = connection.execute(
        "SELECT id, pid FROM review_records WHERE primary_tag IS NULL ORDER BY review_date, id"
    ).fetchall()
    for row in rows:
        tag = primary_tag(connection, row["pid"])
        connection.execute("UPDATE review_records SET primary_tag = ? WHERE id = ?",
                           (tag, row["id"]))


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_problem(connection: sqlite3.Connection, pid: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM problems WHERE pid = ?", (pid,)).fetchone()


def upsert_problem(connection: sqlite3.Connection, pid: str, title: str,
                   difficulty: str | None, tags: Iterable[str] | None) -> None:
    encoded_tags = json.dumps(list(tags or []), ensure_ascii=False)
    connection.execute(
        """
        INSERT INTO problems(pid, title, difficulty, tags, algorithm_tags, technical_tags, all_tags, is_solved, updated_at)
        VALUES (?, ?, ?, ?, ?, '[]', ?, 1, ?)
        ON CONFLICT(pid) DO UPDATE SET
            title = excluded.title,
            difficulty = COALESCE(excluded.difficulty, problems.difficulty),
            tags = CASE WHEN excluded.tags = '[]' THEN problems.tags ELSE excluded.tags END,
            algorithm_tags = CASE WHEN excluded.tags = '[]' THEN problems.algorithm_tags ELSE excluded.algorithm_tags END,
            all_tags = CASE WHEN excluded.tags = '[]' THEN problems.all_tags ELSE excluded.all_tags END,
            is_solved = 1, updated_at = excluded.updated_at
        """,
        (pid, title, difficulty, encoded_tags, encoded_tags, encoded_tags, iso_now()),
    )


def save_problem(connection: sqlite3.Connection, problem: dict) -> None:
    """Save scraped metadata while preserving solved/review state."""
    now = iso_now()
    tags = problem.get("tags", [])
    if tags and isinstance(tags[0], dict):
        tag_names = [tag["name"] for tag in tags]
    else:
        tag_names = list(problem.get("all_tags", tags))
    manager = TagManager(connection)
    manager.initialize()
    connection.execute(
        """
        INSERT INTO problems
          (pid, title, difficulty, algorithm_tags, technical_tags, all_tags,
           tags, source_url, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(pid) DO UPDATE SET
          title=excluded.title, difficulty=excluded.difficulty,
          algorithm_tags=excluded.algorithm_tags, technical_tags=excluded.technical_tags,
          all_tags=excluded.all_tags, tags=excluded.all_tags,
          source_url=excluded.source_url, updated_at=excluded.updated_at
        """,
        (problem["pid"], problem["title"], problem.get("difficulty"),
         json.dumps(problem.get("algorithm_tags", []), ensure_ascii=False),
         json.dumps(problem.get("technical_tags", []), ensure_ascii=False),
         json.dumps(tag_names, ensure_ascii=False),
         json.dumps(tag_names, ensure_ascii=False),
         problem.get("source_url", ""), now),
    )
    connection.execute("DELETE FROM problem_tags WHERE pid = ?", (problem["pid"],))
    for name in tag_names:
        tag = manager.get_or_create_tag(str(name))
        connection.execute(
            "INSERT OR IGNORE INTO problem_tags(pid, tag_id) VALUES (?, ?)",
            (problem["pid"], tag.id),
        )
    algorithm_names = manager.get_algorithm_tags(problem["pid"])
    technical_names = [
        row["name"] for row in connection.execute(
            """SELECT t.name FROM problem_tags pt JOIN tags t ON t.id = pt.tag_id
               WHERE pt.pid = ? AND t.category_l1 != '算法' ORDER BY t.name""",
            (problem["pid"],),
        )
    ]
    connection.execute(
        "UPDATE problems SET algorithm_tags = ?, technical_tags = ? WHERE pid = ?",
        (json.dumps(algorithm_names, ensure_ascii=False),
         json.dumps(technical_names, ensure_ascii=False), problem["pid"]),
    )


def get_problem_with_tags(connection: sqlite3.Connection, pid: str) -> dict | None:
    """Return a problem with all official category metadata."""
    return TagManager(connection).get_problem_with_tags(pid)


def get_problem_algorithm_tags(connection: sqlite3.Connection, pid: str) -> list[str]:
    """Return only tags in the official ``算法`` category."""
    return TagManager(connection).get_algorithm_tags(pid)


def add_review(connection: sqlite3.Connection, pid: str, score: float,
               duration: int | None = None, note: str | None = None,
               wrong_submissions: int = 0, saw_solution: bool = False) -> None:
    tag = primary_tag(connection, pid)
    connection.execute(
        """INSERT INTO review_records
           (pid, score, duration, note, wrong_submissions, saw_solution, primary_tag)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (pid, score, duration, note, wrong_submissions, int(saw_solution), tag),
    )


def primary_tag(connection: sqlite3.Connection, pid: str) -> str | None:
    rows = connection.execute(
        """SELECT t.name, COUNT(r.id) AS practice_count
           FROM problem_tags pt JOIN tags t ON t.id = pt.tag_id
           LEFT JOIN review_records r ON r.pid = pt.pid AND r.primary_tag = t.name
           WHERE pt.pid = ? AND t.category_l1 = '算法'
           GROUP BY t.id ORDER BY practice_count, t.id""", (pid,)
    ).fetchall()
    return rows[0]["name"] if rows else None


def save_card_state(connection: sqlite3.Connection, pid: str, state: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO card_states(pid, stability, difficulty, due_date, last_review, reps)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(pid) DO UPDATE SET
            stability=excluded.stability, difficulty=excluded.difficulty,
            due_date=excluded.due_date, last_review=excluded.last_review,
            reps=excluded.reps
        """,
        (pid, state["stability"], state["difficulty"], state["due_date"],
         state["last_review"], state["reps"]),
    )


def due_problems(connection: sqlite3.Connection, now: str | None = None) -> list[sqlite3.Row]:
    return list(connection.execute(
        """
        SELECT p.*, c.due_date FROM problems p JOIN card_states c ON c.pid = p.pid
        WHERE c.due_date IS NULL OR c.due_date <= ?
        ORDER BY c.due_date IS NULL DESC, c.due_date, p.pid
        """, (now or iso_now(),)
    ))


def backup_database() -> Path | None:
    """Create one daily backup and remove backups older than seven days."""
    if not DB_PATH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().date().isoformat()
    destination = BACKUP_DIR / f"luogu_fsrs_{today}.db"
    if not destination.exists():
        shutil.copy2(DB_PATH, destination)
    cutoff = datetime.now() - timedelta(days=7)
    for backup in BACKUP_DIR.glob("luogu_fsrs_*.db"):
        if datetime.fromtimestamp(backup.stat().st_mtime) < cutoff:
            backup.unlink()
    return destination
