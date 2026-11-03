"""The coffee log itself: one row per coffee round."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Where a tick came from.
FACE = "face"        # recognised on camera
PHONE = "phone"      # someone answered the "who made coffee?" notification
MANUAL = "manual"    # added or corrected on the website
PENDING = "pending"  # grinder ran, nobody knows who yet

SCHEMA = """
CREATE TABLE IF NOT EXISTS brews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,          -- ISO 8601 with offset
    person     TEXT,                   -- NULL while pending
    source     TEXT NOT NULL,
    confidence REAL                    -- face match score, if any
);
CREATE INDEX IF NOT EXISTS brews_ts ON brews(ts);
CREATE TABLE IF NOT EXISTS questions (  -- withHuman requests, one per person per brew
    request_id TEXT PRIMARY KEY,
    brew_id    INTEGER NOT NULL,
    person     TEXT NOT NULL,
    answer     TEXT                    -- NULL while waiting, else the request's final status
);
"""


@dataclass
class Brew:
    id: int
    ts: datetime
    person: str | None
    source: str
    confidence: float | None

    def as_json(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts.isoformat(),
            "person": self.person,
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass
class Question:
    request_id: str
    brew_id: int
    person: str
    answer: str | None


class Log:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.executescript(SCHEMA)

    def add(self, ts: datetime, person: str | None, source: str, confidence: float | None = None) -> Brew:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO brews (ts, person, source, confidence) VALUES (?, ?, ?, ?)",
                (ts.isoformat(), person, source, confidence),
            )
        return Brew(cur.lastrowid, ts, person, source, confidence)

    def get(self, brew_id: int) -> Brew | None:
        row = self.conn.execute("SELECT * FROM brews WHERE id = ?", (brew_id,)).fetchone()
        return _brew(row) if row else None

    def set_person(self, brew_id: int, person: str, source: str, confidence: float | None = None) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE brews SET person = ?, source = ?, confidence = ? WHERE id = ?",
                (person, source, confidence, brew_id),
            )
        return cur.rowcount == 1

    def delete(self, brew_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM brews WHERE id = ?", (brew_id,))
            self.conn.execute("DELETE FROM questions WHERE brew_id = ?", (brew_id,))
        return cur.rowcount == 1

    def recent(self, limit: int = 30) -> list[Brew]:
        rows = self.conn.execute("SELECT * FROM brews ORDER BY ts DESC, id DESC LIMIT ?", (limit,))
        return [_brew(r) for r in rows]

    def pending(self) -> list[Brew]:
        rows = self.conn.execute("SELECT * FROM brews WHERE person IS NULL ORDER BY ts DESC")
        return [_brew(r) for r in rows]

    def totals(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT person, COUNT(*) FROM brews WHERE person IS NOT NULL GROUP BY person")
        return dict(rows.fetchall())

    def last_made(self) -> dict[str, datetime]:
        rows = self.conn.execute("SELECT person, MAX(ts) FROM brews WHERE person IS NOT NULL GROUP BY person")
        return {p: datetime.fromisoformat(ts) for p, ts in rows.fetchall()}

    def add_question(self, request_id: str, brew_id: int, person: str) -> Question:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO questions (request_id, brew_id, person) VALUES (?, ?, ?)",
                (request_id, brew_id, person),
            )
        return Question(request_id, brew_id, person, None)

    def answer_question(self, request_id: str, answer: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE questions SET answer = ? WHERE request_id = ?", (answer, request_id))

    def questions(self, brew_id: int | None = None) -> list[Question]:
        """A brew's questions, or every unanswered one."""
        if brew_id is None:
            rows = self.conn.execute("SELECT * FROM questions WHERE answer IS NULL")
        else:
            rows = self.conn.execute("SELECT * FROM questions WHERE brew_id = ?", (brew_id,))
        return [Question(*r) for r in rows]


def _brew(row) -> Brew:
    return Brew(row[0], datetime.fromisoformat(row[1]), row[2], row[3], row[4])
