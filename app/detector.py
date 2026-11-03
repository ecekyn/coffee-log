"""Turns the grinder plug's power readings into "a coffee round started" events."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass
class Grind:
    at: datetime
    new_round: bool  # False when it's a second grind of the same round (another drink, a re-grind)


class GrindDetector:
    def __init__(self, threshold_w: float, window: tuple[time, time], tz: ZoneInfo, merge: timedelta):
        self.threshold_w = threshold_w
        self.window = window
        self.tz = tz
        self.merge = merge
        self.running = False
        self.last_grind: datetime | None = None

    def in_window(self, now: datetime) -> bool:
        start, end = self.window
        return start <= now.astimezone(self.tz).time() < end

    def prime(self, watts: float | None) -> None:
        """Take the current reading without firing, e.g. after (re)connecting to Home Assistant."""
        if watts is not None:
            self.running = watts >= self.threshold_w

    def update(self, watts: float | None, now: datetime) -> Grind | None:
        """Feed a power reading. Returns a Grind when the grinder has just started."""
        if watts is None:  # sensor unavailable; keep the last known state
            return None
        was_running = self.running
        self.running = watts >= self.threshold_w
        if was_running or not self.running or not self.in_window(now):
            return None
        new_round = self.last_grind is None or now - self.last_grind > self.merge
        self.last_grind = now
        return Grind(now, new_round)


def parse_watts(state: str | None) -> float | None:
    try:
        return float(state)
    except (TypeError, ValueError):  # "unavailable", "unknown", None
        return None
