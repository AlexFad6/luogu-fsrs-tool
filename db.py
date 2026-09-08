"""SQLite persistence for the review tool."""

import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


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
            is_solved INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS review_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pid TEXT NOT NULL REFERENCES problems(pid),
            review_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            score REAL NOT NULL CHECK(score >= 0 AND score <= 1),
            duration INTEGER,
            note TEXT
        );
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
    connection.commit()


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
        INSERT INTO problems(pid, title, difficulty, tags, is_solved)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(pid) DO UPDATE SET
            title = excluded.title,
            difficulty = COALESCE(excluded.difficulty, problems.difficulty),
            tags = CASE WHEN excluded.tags = '[]' THEN problems.tags ELSE excluded.tags END,
            is_solved = 1
        """,
        (pid, title, difficulty, encoded_tags),
    )


def add_review(connection: sqlite3.Connection, pid: str, score: float,
               duration: int | None = None, note: str | None = None) -> None:
    connection.execute(
        "INSERT INTO review_records(pid, score, duration, note) VALUES (?, ?, ?, ?)",
        (pid, score, duration, note),
    )


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
