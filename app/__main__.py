"""Run the coffee log: `python -m app` (real) or `python -m app --demo` (fake data, no Home Assistant)."""

import argparse
import asyncio
import logging
import random
import tempfile
from datetime import timedelta
from pathlib import Path

import aiohttp
from aiohttp import web

from . import config, db, web as website
from .announce import Announcer
from .faces import DETECTOR_MODEL, Faces
from .ha import HomeAssistant
from .service import CoffeeService
from .withhuman import WithHuman

log = logging.getLogger("coffee")


async def main(cfg: config.Config, demo: bool) -> None:
    coffee_log = db.Log(cfg.db_path)
    faces = load_faces(cfg)

    async with aiohttp.ClientSession() as session:
        ha = None if demo else HomeAssistant(cfg, session)
        wh = WithHuman(cfg, session) if cfg.withhuman_token and not demo else None
        announcer = Announcer(cfg, session, ha) if ha and cfg.announce_go2rtc_url else None
        service = CoffeeService(cfg, coffee_log, faces, ha, wh, announcer)
        if demo:
            seed(service)
        service.resume()

        runner = web.AppRunner(website.build(service, demo))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", cfg.port).start()
        log.info("Scoreboard on http://localhost:%d", cfg.port)

        if ha:
            await ha.run(service.on_power_state, service.on_notification_action)
        else:
            await asyncio.Event().wait()


def load_faces(cfg: config.Config) -> Faces | None:
    models = Path(cfg.models_dir)
    if not (models / DETECTOR_MODEL).exists():
        log.warning("Face models not found in %s; every coffee will be asked about on the phones", models)
        return None
    return Faces(models, cfg.faces_dir, cfg.people, cfg.face_threshold)


def seed(service: CoffeeService) -> None:
    """Three weeks of made-up mornings so the page has something to show."""
    rng = random.Random(7)
    today = service.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for days_ago in range(21, 0, -1):
        if rng.random() < 0.15:
            continue  # nobody bothered
        at = today - timedelta(days=days_ago) + timedelta(hours=6, minutes=rng.randint(30, 89))
        person = rng.choices(service.cfg.people, weights=[5, 4])[0]
        source = rng.choices([db.FACE, db.PHONE, db.MANUAL], weights=[8, 2, 1])[0]
        service.log.add(at, person, source, round(rng.uniform(0.45, 0.8), 2) if source == db.FACE else None)
    service.log.add(today - timedelta(days=2) + timedelta(hours=7, minutes=48), None, db.PENDING)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="fake data, no Home Assistant")
    parser.add_argument("--config", help="JSON settings file (default: $COFFEE_CONFIG or /data/options.json)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load(args.config)
    if args.demo:
        cfg.data_dir = tempfile.mkdtemp(prefix="coffee-demo-")
        if not Path(cfg.models_dir).exists():
            cfg.models_dir = str(Path(__file__).parent.parent / "models")
    try:
        asyncio.run(main(cfg, args.demo))
    except KeyboardInterrupt:
        pass
