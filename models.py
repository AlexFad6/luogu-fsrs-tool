"""Data models used by the Luogu FSRS tool."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Problem:
    pid: str
    title: str
    difficulty: Optional[str] = None
    tags: list[str] | None = None
    is_solved: bool = False


@dataclass
class CardState:
    pid: str
    stability: Optional[float]
    difficulty: Optional[float]
    due_date: Optional[datetime]
    last_review: Optional[datetime]
    reps: int
