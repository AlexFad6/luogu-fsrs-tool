"""Adapter around the fsrs package."""

from datetime import timezone
from fsrs import Card, Rating, Scheduler, State


RATING_MAP = {0.0: Rating.Again, 0.3: Rating.Hard, 0.5: Rating.Good,
              0.8: Rating.Easy, 1.0: Rating.Easy}


def rating_for_score(score: float) -> Rating:
    """Map a user score to the closest supported FSRS rating."""
    closest_score = min(RATING_MAP, key=lambda value: abs(value - score))
    return RATING_MAP[closest_score]


def state_from_card(card: Card, reps: int) -> dict:
    def as_iso(value):
        return value.astimezone(timezone.utc).isoformat() if value else None
    return {
        "stability": card.stability,
        "difficulty": card.difficulty,
        "due_date": as_iso(card.due),
        "last_review": as_iso(card.last_review),
        "reps": reps,
    }


def initial_state(score: float) -> dict:
    scheduler = Scheduler()
    card = scheduler.review_card(Card(), rating_for_score(score))[0]
    return state_from_card(card, 1)


def review_state(existing: dict, score: float) -> dict:
    return review_state_by_rating(existing, rating_for_score(score))


def review_state_by_rating(existing: dict, rating: Rating) -> dict:
    """Update a card directly from an FSRS rating."""
    card = Card()
    for key in ("stability", "difficulty", "due", "last_review"):
        if key in existing and hasattr(card, key):
            setattr(card, key, existing[key])
    if existing.get("reps", 0) > 0:
        card.state = State.Review
    updated = Scheduler().review_card(card, rating)[0]
    return state_from_card(updated, existing.get("reps", 0) + 1)
