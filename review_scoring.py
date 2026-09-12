"""Infer ratings from append-only initial and review attempt events.

Initial events are used to create the FSRS card and initialize the timing
baselines, but never enter the review improvement formula. Review events update
the problem baseline; pooled difficulty data is only used for cold starts.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

import yaml
from fsrs import Rating


def _config() -> dict:
    with Path(__file__).with_name("config.yaml").open(encoding="utf-8") as stream:
        return (yaml.safe_load(stream) or {}).get("review_scoring", {})


def _difficulty_name(value: object) -> str:
    return str(value or "").replace("-", "−")


def _floor_values(cfg: dict, phase: str = "review") -> dict:
    floors = cfg.get("difficulty_floor_min", {})
    values = floors.get(phase) if isinstance(floors, dict) else None
    return values if isinstance(values, dict) else floors


def global_floor() -> float:
    cfg = _config()
    values = []
    for phase in ("initial", "review"):
        values.extend(float(value) for value in _floor_values(cfg, phase).values()
                      if isinstance(value, (int, float)))
    return min(values or [float(cfg.get("floor_min", 5))])


def configured_floor(difficulty: object, phase: str = "review") -> float:
    values = _floor_values(_config(), phase)
    return float(values.get(_difficulty_name(difficulty), global_floor()))


def _duration(row: dict) -> float | None:
    value = row.get("duration")
    return float(value) if value is not None and float(value) > 0 else None


def pool_baseline(pool: list[dict], difficulty: object) -> float:
    """Return L1: median of per-problem minimum attempts for this difficulty."""
    grouped: dict[str, list[float]] = {}
    target = _difficulty_name(difficulty)
    for row in pool:
        if _difficulty_name(row.get("difficulty")) != target:
            continue
        duration = _duration(row)
        if duration is not None:
            grouped.setdefault(str(row.get("pid", "")), []).append(duration)
    minimums = [min(values) for values in grouped.values() if values]
    if len(minimums) < int(_config().get("pool_min_problems", 3)):
        return global_floor()
    return median(minimums)


def _alpha() -> float:
    half_life = float(_config().get("ewma_half_life", 2))
    return 1 - 2 ** (-1 / half_life)


def baselines(history: list[dict], current: dict) -> tuple[float, float, float]:
    """Return L0 global fallback, L1 difficulty pool, and L2 problem values."""
    difficulty = current.get("difficulty")
    pool = current.get("_pool_history") or history
    l0 = global_floor()
    l1 = pool_baseline(pool, difficulty)
    durations = [value for row in history if (value := _duration(row)) is not None]
    floor = max(l0, min(durations or [l0]))
    initial = next(
        (value for row in history
         if row.get("attempt_type", "review") == "initial"
         for value in [_duration(row)] if value is not None),
        None,
    )
    reviews = [value for row in history
               if row.get("attempt_type", "review") == "review"
               for value in [_duration(row)] if value is not None]
    values = ([initial] if initial is not None else []) + reviews
    baseline = _ewma(values, _alpha()) if values else max(l1, floor)
    return l0, l1, max(floor, baseline)


def timing_metrics(history: list[dict], current: dict) -> tuple[float, float, int]:
    """Return the problem floor, L2 baseline and total attempt count."""
    l0, _, baseline = baselines(history, current)
    durations = [value for row in history if (value := _duration(row)) is not None]
    return max(l0, min(durations or [l0])), baseline, len(history)


def _ewma(values: list[float], alpha: float) -> float:
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _is_fresh(current: dict) -> bool:
    if not _config().get("freshness_cap", True) or not current.get("due_date"):
        return False
    due = current["due_date"]
    if not isinstance(due, datetime):
        due = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return due > datetime.now(timezone.utc)


def infer_rating(history: list[dict], current: dict) -> tuple[Rating, str]:
    """Infer a rating; initial events use a pool-based cold-start rule."""
    cfg = _config()
    if current.get("saw_solution"):
        return Rating.Again, "看过题解 -> Again"
    reviews = [row for row in history if row.get("attempt_type", "review") == "review"]
    previous = reviews[-1] if reviews else None
    wrong = int(current.get("wrong_submissions") or 0)
    previous_wrong = int(previous.get("wrong_submissions") or 0) if previous else 0
    if wrong > previous_wrong + int(cfg.get("wrong_again_margin", 1)):
        return Rating.Again, f"错误提交增加超过阈值 ({wrong}>{previous_wrong}+margin)"
    if wrong >= int(cfg.get("wrong_hard", 2)):
        return Rating.Hard, f"错误提交达到阈值 ({wrong})"

    duration = _duration(current) or global_floor()
    if current.get("attempt_type") == "initial" or not history:
        pool = pool_baseline(current.get("_pool_history") or history, current.get("difficulty"))
        rating = Rating.Good if duration <= pool else Rating.Hard
        return rating, f"冷启动：用时 {duration:g}，L1={pool:g} 分钟 -> {rating.name}"
    if previous is None:
        return Rating.Good, "评分： Good"

    l0, l1, baseline = baselines(history, current)
    durations = [value for row in history if (value := _duration(row)) is not None]
    floor = max(l0, min(durations or [l0]))
    epsilon = float(cfg.get("excess_epsilon", 0.5))
    excess_baseline = max(baseline, floor) - floor + epsilon
    excess_current = max(duration, floor) - floor + epsilon
    improvement = math.log2(excess_baseline / excess_current)
    if improvement >= float(cfg.get("imp_easy", 1.0)):
        rating = Rating.Easy
    elif improvement >= float(cfg.get("imp_good", 0.0)):
        rating = Rating.Good
    elif improvement >= float(cfg.get("imp_hard", -1.0)):
        rating = Rating.Hard
    else:
        rating = Rating.Again
    if len(reviews) == 1:
        rating = Rating.Good if rating == Rating.Easy else rating
    if _is_fresh(current):
        rating = Rating.Good if rating == Rating.Easy else rating
    return rating, (
        f"floor={floor:.2f}, L1={l1:.2f}, baseline={baseline:.2f}, "
        f"excess={excess_current:.2f}, imp={improvement:.2f} -> {rating.name}"
    )
