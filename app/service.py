"""Ties it together: grinder starts -> look at the camera -> tick the log, or ask on the phones."""

import asyncio
import logging
from datetime import datetime, timedelta

from . import db
from .config import Config
from .detector import Grind, GrindDetector, parse_watts
from .faces import Faces, Match
from .ha import HomeAssistant
from .turns import lead, whose_turn

log = logging.getLogger(__name__)

ACTION_PREFIX = "COFFEE_"
NOT_COFFEE = "X"


class CoffeeService:
    def __init__(self, cfg: Config, log_: db.Log, faces: Faces | None, ha: HomeAssistant | None):
        self.cfg = cfg
        self.log = log_
        self.faces = faces
        self.ha = ha
        self.detector = GrindDetector(cfg.power_threshold_w, cfg.window, cfg.tz, timedelta(minutes=cfg.merge_minutes))
        self.round_brew_id: int | None = None
        self._tasks: set[asyncio.Task] = set()

    def now(self) -> datetime:
        return datetime.now(self.cfg.tz)

    # --- Home Assistant callbacks ---

    async def on_power_state(self, state: str | None, initial: bool) -> None:
        watts = parse_watts(state)
        if initial:
            self.detector.prime(watts)
            return
        grind = self.detector.update(watts, self.now())
        if grind:
            # Looking at the camera takes a few seconds; don't hold up the Home Assistant feed meanwhile.
            task = asyncio.create_task(self.on_grind(grind))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def on_notification_action(self, action: str) -> None:
        if not action.startswith(ACTION_PREFIX):
            return  # someone else's notification
        try:
            brew_id, choice = action.removeprefix(ACTION_PREFIX).split("_")
            brew_id = int(brew_id)
        except ValueError:
            return
        if choice == NOT_COFFEE:
            self.log.delete(brew_id)
            log.info("Brew %d removed from phone (not coffee)", brew_id)
        elif choice.isdigit() and int(choice) < len(self.cfg.people):
            person = self.cfg.people[int(choice)]
            self.log.set_person(brew_id, person, db.PHONE)
            log.info("Brew %d: %s, answered on phone", brew_id, person)
        await self.clear_question(brew_id)

    # --- the main flow ---

    async def on_grind(self, grind: Grind) -> None:
        if grind.new_round:
            brew = self.log.add(grind.at, None, db.PENDING)
            self.round_brew_id = brew.id
            log.info("Grinder started at %s, new coffee round #%d", grind.at.strftime("%H:%M"), brew.id)
        else:
            brew = self.log.get(self.round_brew_id) if self.round_brew_id else None
            if brew is None or brew.person is not None:
                return  # already know who made this round (or it was deleted)
            log.info("Grinder again for round #%d, taking another look", brew.id)

        match = await self.look(brew.id)
        log.info("Round #%d: %s", brew.id, match.reason)
        if match.person:
            self.log.set_person(brew.id, match.person, db.FACE, match.score)
            if not grind.new_round:
                await self.clear_question(brew.id)  # we asked after the first grind
        elif grind.new_round:
            await self.ask(brew.id, match)

    async def look(self, brew_id: int) -> Match:
        if not self.faces or not self.ha:
            return Match(None)
        frames = []
        for i in range(self.cfg.snapshot_count):
            if i:
                await asyncio.sleep(self.cfg.snapshot_interval_s)
            try:
                frames.append(await self.ha.snapshot())
            except Exception as e:
                log.warning("Couldn't get a camera frame: %s", e)
        if self.cfg.keep_snapshots:
            self.cfg.snapshots_dir.mkdir(parents=True, exist_ok=True)
            for i, frame in enumerate(frames):
                (self.cfg.snapshots_dir / f"{brew_id}-{i}.jpg").write_bytes(frame)
        return await asyncio.to_thread(self.faces.identify, frames)

    async def ask(self, brew_id: int, match: Match) -> None:
        if not self.ha or not self.cfg.notify_services:
            return
        actions = [{"action": f"{ACTION_PREFIX}{brew_id}_{i}", "title": p} for i, p in enumerate(self.cfg.people)]
        actions.append({"action": f"{ACTION_PREFIX}{brew_id}_{NOT_COFFEE}", "title": "Not coffee"})
        data = {"actions": actions, "tag": f"coffee-{brew_id}", "group": "coffee"}
        for service in self.cfg.notify_services:
            await self.ha.notify(service, f"Grinder's going ({match.reason}). Who's making it?", data, "☕ Who made coffee?")

    async def clear_question(self, brew_id: int) -> None:
        if not self.ha:
            return
        for service in self.cfg.notify_services:
            await self.ha.notify(service, "clear_notification", {"tag": f"coffee-{brew_id}"})

    # --- for the website ---

    def scoreboard(self) -> dict:
        people = self.cfg.people
        totals = self.log.totals()
        now = self.now()
        recent = self.log.recent(40)
        made_today = any(b.person and b.ts.astimezone(self.cfg.tz).date() == now.date() for b in recent)
        _, window_end = self.cfg.window
        leader, by = lead(people, totals)
        return {
            "people": people,
            "totals": {p: totals.get(p, 0) for p in people},
            "turn": whose_turn(people, totals, self.log.last_made()),
            "turn_when": "today" if not made_today and now.time() < window_end else "tomorrow",
            "lead": {"person": leader, "by": by},
            "recent": [b.as_json() for b in recent],
            "pending": [b.as_json() for b in self.log.pending()],
            "window": {"start": self.cfg.window_start, "end": self.cfg.window_end},
            "now": now.isoformat(),
            "status": {
                "home_assistant": bool(self.ha and self.ha.connected),
                "faces": self.faces is not None,
                "references": {p: len(self.faces.references.get(p, [])) for p in people} if self.faces else {},
            },
        }
