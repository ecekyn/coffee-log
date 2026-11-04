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
        web.post("/api/brews", add_brew),
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


async def add_brew(request: web.Request) -> web.Response:
    svc = request.app[SERVICE]
    person = _person(svc, (await request.json()).get("person"))
    brew = svc.log.add(svc.now(), person, db.MANUAL)
    return web.json_response(brew.as_json(), status=201)


async def fix_brew(request: web.Request) -> web.Response:
    svc = request.app[SERVICE]
    brew_id = int(request.match_info["id"])
    person = _person(svc, (await request.json()).get("person"))
    if not svc.log.set_person(brew_id, person, db.MANUAL):
        raise web.HTTPNotFound(text="No such brew")
    await svc.clear_question(brew_id)
    return web.json_response(svc.log.get(brew_id).as_json())


async def delete_brew(request: web.Request) -> web.Response:
    svc = request.app[SERVICE]
    brew_id = int(request.match_info["id"])
    if not svc.log.delete(brew_id):
        raise web.HTTPNotFound(text="No such brew")
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


def _person(svc: CoffeeService, person) -> str:
    if person not in svc.cfg.people:
        raise web.HTTPBadRequest(text=f"person must be one of {svc.cfg.people}")
    return person
