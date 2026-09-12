import sqlite3

import db
from fsrs import Rating
from review_scoring import infer_rating
from review_scoring import baselines


def setup_connection():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.initialize_database(connection)
    db.save_problem(connection, {"pid": "P1001", "title": "A+B", "difficulty": "入门",
                                 "all_tags": []})
    return connection


def test_attempt_type_and_retrievability_are_persisted():
    connection = setup_connection()
    db.add_review(connection, "P1001", 0.5, duration=4, attempt_type="initial",
                  retrievability=0.9)
    db.add_review(connection, "P1001", 0.8, duration=2, attempt_type="review",
                  retrievability=0.4)
    attempts = db.get_attempts(connection, "P1001")
    assert [row["attempt_type"] for row in attempts] == ["initial", "review"]
    assert attempts[-1]["retrievability"] == 0.4
    assert len(db.get_all_attempts_for_stats(connection)) == 2


def test_solution_and_errors_have_priority_over_timing():
    assert infer_rating([], {"saw_solution": True})[0] == Rating.Again
    history = [{"attempt_type": "review", "duration": 1, "wrong_submissions": 0}]
    assert infer_rating(history, {"duration": 1, "wrong_submissions": 3})[0] == Rating.Again


def test_legacy_is_initial_rows_are_migrated():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
      CREATE TABLE problems (pid TEXT PRIMARY KEY, title TEXT NOT NULL, difficulty TEXT,
        tags TEXT, is_solved INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
      CREATE TABLE review_records (id INTEGER PRIMARY KEY AUTOINCREMENT, pid TEXT NOT NULL,
        review_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, score REAL NOT NULL,
        duration INTEGER, note TEXT, wrong_submissions INTEGER NOT NULL DEFAULT 0,
        saw_solution INTEGER NOT NULL DEFAULT 0, primary_tag TEXT,
        is_initial INTEGER NOT NULL DEFAULT 0);
    """)
    connection.execute("INSERT INTO problems(pid,title) VALUES ('P1','P1')")
    connection.execute("INSERT INTO review_records(pid,score,is_initial) VALUES ('P1',.5,1)")
    db.initialize_database(connection)
    assert db.get_attempts(connection, "P1")[0]["attempt_type"] == "initial"
    assert "is_initial" not in {
        row["name"] for row in connection.execute("PRAGMA table_info(review_records)")
    }


def test_baselines_pool_reviews_and_include_initial_in_l2():
    history = [
        {"pid": "P1", "difficulty": "入门", "attempt_type": "initial", "duration": 10},
        {"pid": "P1", "difficulty": "入门", "attempt_type": "review", "duration": 8},
    ]
    pool = history + [
        {"pid": "P2", "difficulty": "入门", "attempt_type": "review", "duration": 2},
        {"pid": "P3", "difficulty": "入门", "attempt_type": "review", "duration": 2},
    ]
    l0, l1, l2 = baselines(history, {"difficulty": "入门", "_pool_history": pool})
    assert l0 < l1
    assert l2 > l1
