"""Settings, read from the add-on's options.json or a JSON file given by COFFEE_CONFIG."""

import json
import os
from dataclasses import dataclass, field, fields
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass
class Config:
    people: list[str] = field(default_factory=lambda: ["Ece", "Chris"])

    # Home Assistant. Inside an add-on these are filled in from SUPERVISOR_TOKEN.
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str = ""

    # The P110's power sensor, and the camera pointed at the coffee corner.
    power_sensor: str = "sensor.grinder_current_consumption"
    power_threshold_w: float = 20.0
    camera_entity: str = "camera.kitchen"

    # Only grinds inside this window count as "the morning coffee".
    window_start: str = "06:30"
    window_end: str = "08:00"
    timezone: str = "Europe/London"

    # Grinds this close together are one coffee round (two drinks, a re-grind, ...).
    merge_minutes: int = 15

    # notify.mobile_app_* services to ask "who made coffee?" when the camera can't tell.
    notify_services: list[str] = field(default_factory=list)

    # Or ask through withHuman instead: an API key for the coffee-log agent's instance. Each person
    # gets their own request, so the entry pipeline must route on arguments.person (see README).
    withhuman_url: str = "https://app.withhuman.ai"
    withhuman_token: str = ""
    withhuman_timeout: str = "12h"

    # Face matching. 0.363 is OpenCV's recommended cosine threshold for SFace.
    face_threshold: float = 0.363
    snapshot_count: int = 4
    snapshot_interval_s: float = 1.0
    keep_snapshots: bool = False

    data_dir: str = "/data"
    models_dir: str = "/models"
    port: int = 8099

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def window(self) -> tuple[time, time]:
        return time.fromisoformat(self.window_start), time.fromisoformat(self.window_end)

    @property
    def faces_dir(self) -> Path:
        return Path(self.data_dir) / "faces"

    @property
    def snapshots_dir(self) -> Path:
        return Path(self.data_dir) / "snapshots"

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "coffee.db"

    @property
    def in_addon(self) -> bool:
        return bool(os.environ.get("SUPERVISOR_TOKEN"))

    @property
    def api_url(self) -> str:
        if self.in_addon:
            return "http://supervisor/core/api"
        return self.ha_url.rstrip("/") + "/api"

    @property
    def ws_url(self) -> str:
        if self.in_addon:
            return "ws://supervisor/core/websocket"
        return self.ha_url.rstrip("/").replace("http", "ws", 1) + "/api/websocket"

    @property
    def token(self) -> str:
        return os.environ.get("SUPERVISOR_TOKEN") or self.ha_token


def load(path: str | None = None) -> Config:
    path = path or os.environ.get("COFFEE_CONFIG") or "/data/options.json"
    raw = {}
    if Path(path).exists():
        raw = json.loads(Path(path).read_text())
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"Unknown settings in {path}: {', '.join(sorted(unknown))}")
    cfg = Config(**raw)
    # Under systemd (the NixOS module) the tokens come as credentials rather than in the file.
    creds = os.environ.get("CREDENTIALS_DIRECTORY")
    for name in ("ha_token", "withhuman_token"):
        secret = Path(creds or "/nonexistent") / name
        if not getattr(cfg, name) and secret.exists():
            setattr(cfg, name, secret.read_text().strip())
    if len(cfg.people) != len(set(cfg.people)) or len(cfg.people) < 2:
        raise ValueError("people needs at least two distinct names")
    cfg.window  # fail early on a badly written time
    return cfg
