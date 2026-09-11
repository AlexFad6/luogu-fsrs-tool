"""Infer FSRS ratings from a user's cross-attempt timing data."""

from __future__ import annotations

import math
from pathlib import Path
from statistics import median
import yaml
from fsrs import Rating


def _config() -> dict:
    with Path(__file__).with_name("config.yaml").open(encoding="utf-8") as stream:
        return (yaml.safe_load(stream) or {}).get("review_scoring", {})


def _difficulty_name(value: object) -> str:
    """Normalize the two common spellings of Luogu's minus sign."""
    return str(value or "").replace("-", "−")


def configured_floor(difficulty: object) -> float:
    cfg = _config()
    floors = cfg.get("difficulty_floor_min", {})
    return float(floors.get(_difficulty_name(difficulty), cfg.get("floor_min", 5)))


def _review_floor(history: list[dict], difficulty: object) -> float:
    base = configured_floor(difficulty)
    durations = [
        float(row["duration"]) for row in history
        if not row.get("is_initial") and row.get("duration") is not None
        and float(row["duration"]) > 0
    ]
    if not durations:
        return base
    ratio = float(_config().get("adaptive_floor_ratio", 0.25))
    return max(base, median(durations) * ratio)


def infer_rating(history: list[dict], current: dict) -> tuple[Rating, str]:
    """Infer a rating and a human-readable decision trace."""
    cfg = _config()
    if current.get("saw_solution"):
        return Rating.Again, "看过题解 -> Again"
    if current.get("is_initial"):
        return Rating.Good, "首次做题不与复习用时比较 -> Good"
    difficulty = current.get("difficulty")
    review_history = [row for row in history if not row.get("is_initial")]
    previous = review_history[-1] if review_history else None
    if previous is None:
        return Rating.Good, "无历史记录 -> 首次复习封顶为 Good"
    floor = _review_floor(history, difficulty)
    previous_duration = max(previous.get("duration") or floor, floor)
    current_duration = max(current.get("duration") or floor, floor)
    improvement = math.log2(previous_duration / current_duration)
    wrong = current.get("wrong_submissions", 0)
    previous_wrong = previous.get("wrong_submissions", 0)
    if wrong > previous_wrong + cfg.get("wrong_again_margin", 1):
        return Rating.Again, f"错误提交增加超过阈值 ({wrong}>{previous_wrong}+margin)"
    if wrong >= cfg.get("wrong_hard", 2):
        return Rating.Hard, f"错误提交达到阈值 ({wrong})"
    if improvement >= cfg.get("imp_easy", 1.0):
        rating = Rating.Easy
    elif improvement >= cfg.get("imp_good", 0.0):
        rating = Rating.Good
    elif improvement >= cfg.get("imp_hard", -1.0):
        rating = Rating.Hard
    else:
        rating = Rating.Again
    if not review_history and rating == Rating.Easy:
        rating = Rating.Good
    return rating, (
        f"难度 {difficulty or '未设置'} 最短用时 {floor:g} 分钟，"
        f"用时 {previous_duration:g}->{current_duration:g} 分钟，"
        f"imp={improvement:.2f} -> {rating.name}"
    )
