"""Whose turn is it? Whoever has made fewer coffees in total."""

from datetime import datetime

NEVER = datetime.min


def whose_turn(people: list[str], totals: dict[str, int], last_made: dict[str, datetime]) -> str:
    """Fewest total ticks goes next. On a tie, whoever made one least recently (or never)."""

    def key(person: str):
        last = last_made.get(person)
        # Compare naive and aware datetimes safely: never-made sorts first.
        return (totals.get(person, 0), last.timestamp() if last else float("-inf"))

    return min(people, key=key)


def lead(people: list[str], totals: dict[str, int]) -> tuple[str | None, int]:
    """Who is ahead, and by how many. (None, 0) when level."""
    ranked = sorted(people, key=lambda p: totals.get(p, 0), reverse=True)
    gap = totals.get(ranked[0], 0) - totals.get(ranked[1], 0)
    return (ranked[0], gap) if gap else (None, 0)
