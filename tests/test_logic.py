from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.detector import GrindDetector, parse_watts
from app.turns import lead, whose_turn

LONDON = ZoneInfo("Europe/London")
PEOPLE = ["Ece", "Chris"]


def at(hh, mm, day=3):
    return datetime(2026, 11, day, hh, mm, tzinfo=LONDON)


def detector():
    return GrindDetector(20, (time(6, 30), time(8, 0)), LONDON, timedelta(minutes=15))


def test_grind_starts_a_round_once_per_burst():
    d = detector()
    assert d.update(1.2, at(7, 0)) is None
    g = d.update(180, at(7, 1))
    assert g and g.new_round
    assert d.update(175, at(7, 1)) is None  # still grinding
    assert d.update(0.8, at(7, 1)) is None  # stopped


def test_second_grind_soon_after_is_same_round():
    d = detector()
    d.update(180, at(7, 0)); d.update(0, at(7, 0))
    g = d.update(180, at(7, 10))
    assert g and not g.new_round
    d.update(0, at(7, 10))
    g = d.update(180, at(7, 30))  # 20 min after the last grind
    assert g and g.new_round


def test_outside_window_is_ignored():
    d = detector()
    assert d.update(180, at(6, 29)) is None
    d.update(0, at(6, 29))
    assert d.update(180, at(8, 0)) is None


def test_window_uses_local_time():
    d = detector()
    utc = datetime(2026, 7, 1, 6, 0, tzinfo=ZoneInfo("UTC"))  # 07:00 in London (BST)
    assert d.update(180, utc).new_round


def test_unavailable_sensor_keeps_state():
    d = detector()
    d.update(180, at(7, 0))
    assert d.update(parse_watts("unavailable"), at(7, 0)) is None
    assert d.update(180, at(7, 0)) is None  # not a new start
    assert parse_watts("12.5") == 12.5


def test_prime_does_not_fire():
    d = detector()
    d.prime(180)
    assert d.update(180, at(7, 0)) is None


def test_fewer_ticks_goes_next():
    assert whose_turn(PEOPLE, {"Ece": 5, "Chris": 3}, {}) == "Chris"
    assert whose_turn(PEOPLE, {"Ece": 2}, {}) == "Chris"


def test_tie_goes_to_whoever_made_one_longest_ago():
    last = {"Ece": at(7, 0, day=2), "Chris": at(7, 0, day=3)}
    assert whose_turn(PEOPLE, {"Ece": 4, "Chris": 4}, last) == "Ece"
    assert whose_turn(PEOPLE, {}, {}) == "Ece"


def test_lead():
    assert lead(PEOPLE, {"Ece": 7, "Chris": 4}) == ("Ece", 3)
    assert lead(PEOPLE, {"Ece": 4, "Chris": 4}) == (None, 0)
