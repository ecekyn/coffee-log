"""The scoreboard website and its small JSON API. All URLs are relative so it also works behind HA ingress."""

import asyncio
import logging
from pathlib import Path

from aiohttp import web

from . import db
from .detector import Grind
from .service import CoffeeService

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
SERVICE = web.AppKey("service", CoffeeService)
DEMO = web.AppKey("demo", bool)
ENROLL_FRAMES = 5
ENROLL_INTERVAL_S = 1.0


def build(service: CoffeeService, demo: bool = False) -> web.Application:
    app = web.Application(client_max_size=12 * 1024 * 1024)  # phone photos for enrolment
    app[SERVICE] = service
    app[DEMO] = demo
    app.add_routes([
        web.get("/", index),
        web.get("/api/state", state),
        web.patch("/api/brews/{id}", fix_brew),
        web.delete("/api/brews/{id}", delete_brew),
        web.post("/api/enroll/{person}", enroll),
    ])
    if demo:
        app.add_routes([web.post("/api/demo/grind", demo_grind)])
    return app


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


async def state(request: web.Request) -> web.Response:
    return web.json_response(request.app[SERVICE].scoreboard() | {"demo": request.app[DEMO]})


# Ticks come from the camera. The website can only answer for a coffee the camera couldn't place.


async def fix_brew(request: web.Request) -> web.Response:
    """Answer "who made it?" for a coffee the camera couldn't place."""
    svc = request.app[SERVICE]
    brew_id = _unanswered(svc, request)
    person = _person(svc, (await request.json()).get("person"))
    svc.log.set_person(brew_id, person, db.MANUAL)
    await svc.clear_question(brew_id)
    return web.json_response(svc.log.get(brew_id).as_json())


async def delete_brew(request: web.Request) -> web.Response:
    """Not coffee: drop a grind the camera couldn't place."""
    svc = request.app[SERVICE]
    brew_id = _unanswered(svc, request)
    svc.log.delete(brew_id)
    await svc.clear_question(brew_id)
    return web.Response(status=204)


async def enroll(request: web.Request) -> web.Response:
    """Add a reference photo: an uploaded image, or (empty body) a frame from the kitchen camera."""
    svc = request.app[SERVICE]
    person = _person(svc, request.match_info["person"])
    if not svc.faces:
        return web.json_response({"ok": False, "message": "Face recognition isn't running (models missing?)."})
    image = await request.read()
    if not image:
        if not svc.ha:
            return web.json_response({"ok": False, "message": "Not connected to Home Assistant, so no camera."})
        try:
            # A few seconds of frames, so there's time to lower the phone that tapped the button.
            frames = await svc.ha.frames(ENROLL_FRAMES, ENROLL_INTERVAL_S)
        except Exception as e:
            return web.json_response({"ok": False, "message": f"Couldn't get a camera frame: {e}"})
        image = await asyncio.to_thread(svc.faces.clearest, frames)
    ok, message = await asyncio.to_thread(svc.faces.enroll, person, image)
    return web.json_response({"ok": ok, "message": message})


async def demo_grind(request: web.Request) -> web.Response:
    svc = request.app[SERVICE]
    await svc.on_grind(Grind(svc.now(), new_round=True))
    return web.Response(status=204)


def _unanswered(svc: CoffeeService, request: web.Request) -> int:
    brew = svc.log.get(int(request.match_info["id"]))
    if brew is None:
        raise web.HTTPNotFound(text="No such brew")
    if brew.person is not None:
        raise web.HTTPConflict(text="The camera already ticked this one")
    return brew.id


def _person(svc: CoffeeService, person) -> str:
    if person not in svc.cfg.people:
        raise web.HTTPBadRequest(text=f"person must be one of {svc.cfg.people}")
    return person
