"""The whole flow against a fake Home Assistant: a real websocket + REST server speaking HA's protocol."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app import db
from app.config import Config
from app.faces import Match
from app.ha import HomeAssistant
from app.service import CoffeeService


class FakeHA:
    """Just enough of Home Assistant: auth, subscriptions, entity state, camera, notify."""

    def __init__(self):
        self.ws = None
        self.subscribed = asyncio.Event()
        self.notifications: list[tuple[str, dict]] = []
        self.app = web.Application()
        self.app.add_routes([
            web.get("/api/websocket", self.websocket),
            web.get("/api/states/{entity}", self.state),
            web.get("/api/camera_proxy/{entity}", self.camera),
            web.post("/api/services/notify/{name}", self.notify),
        ])

    async def websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        auth = await ws.receive_json()
        if auth["access_token"] != "good":
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            return ws
        await ws.send_json({"type": "auth_ok"})
        for _ in range(2):
            sub = await ws.receive_json()
            await ws.send_json({"id": sub["id"], "type": "result", "success": True})
        self.ws = ws
        self.subscribed.set()
        async for _ in ws:
            pass
        return ws

    async def power(self, watts):
        await self.ws.send_json({"id": 1, "type": "event",
                                 "event": {"variables": {"trigger": {"to_state": {"state": str(watts)}}}}})

    async def tap(self, action):
        await self.ws.send_json({"id": 2, "type": "event",
                                 "event": {"event_type": "mobile_app_notification_action", "data": {"action": action}}})

    async def state(self, request):
        return web.json_response({"state": "0.9"})

    async def camera(self, request):
        return web.Response(body=b"jpeg", content_type="image/jpeg")

    async def notify(self, request):
        self.notifications.append((request.match_info["name"], await request.json()))
        return web.json_response([])


class FakeFaces:
    def __init__(self, answer: Match):
        self.answer = answer
        self.references = {}
        self.frames_seen = 0

    def identify(self, frames):
        self.frames_seen += len(frames)
        return self.answer


@pytest.fixture
async def fake_ha():
    ha = FakeHA()
    server = TestServer(ha.app)
    await server.start_server()
    yield ha, f"http://{server.host}:{server.port}"
    await server.close()


async def until(check, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition never became true")


async def start(fake_ha, match, **cfg):
    ha_fake, url = fake_ha
    config = Config(ha_url=url, ha_token="good", window_start="00:00", window_end="23:59",
                    snapshot_count=2, snapshot_interval_s=0,
                    notify_services=["notify.mobile_app_ece", "notify.mobile_app_chris"], **cfg)
    session = aiohttp.ClientSession()
    client = HomeAssistant(config, session)
    service = CoffeeService(config, db.Log(":memory:"), FakeFaces(match), client)
    task = asyncio.create_task(client.run(service.on_power_state, service.on_notification_action))
    await asyncio.wait_for(ha_fake.subscribed.wait(), 3)

    async def stop():
        task.cancel()
        await session.close()
    return ha_fake, service, stop


async def test_recognised_face_ticks_the_log(fake_ha):
    ha, service, stop = await start(fake_ha, Match("Chris", 0.61, {"Chris": 0.61}, 1))
    await ha.power(185)
    await until(lambda: service.log.totals() == {"Chris": 1})
    brew = service.log.recent()[0]
    assert brew.source == db.FACE and brew.confidence == 0.61
    assert service.faces.frames_seen == 2
    assert ha.notifications == []
    assert service.scoreboard()["turn"] == "Ece"
    await stop()


async def test_unknown_asks_both_phones_and_answer_ticks(fake_ha):
    ha, service, stop = await start(fake_ha, Match(None, faces=0))
    await ha.power(185)
    await until(lambda: len(ha.notifications) == 2)
    brew = service.log.pending()[0]
    service_names = [n for n, _ in ha.notifications]
    assert service_names == ["mobile_app_ece", "mobile_app_chris"]
    buttons = [a["title"] for a in ha.notifications[0][1]["data"]["actions"]]
    assert buttons == ["Ece", "Chris", "Not coffee"]

    await ha.tap(f"COFFEE_{brew.id}_0")
    await until(lambda: service.log.totals() == {"Ece": 1})
    assert service.log.get(brew.id).source == db.PHONE
    await until(lambda: len(ha.notifications) == 4)  # cleared on both phones
    assert ha.notifications[-1][1] == {"message": "clear_notification", "data": {"tag": f"coffee-{brew.id}"}}
    await stop()


async def test_not_coffee_deletes(fake_ha):
    ha, service, stop = await start(fake_ha, Match(None))
    await ha.power(185)
    await until(lambda: service.log.pending())
    brew_id = service.log.pending()[0].id
    await ha.tap(f"COFFEE_{brew_id}_X")
    await until(lambda: service.log.get(brew_id) is None)
    await stop()


async def test_regrind_in_same_round_is_one_tick(fake_ha):
    ha, service, stop = await start(fake_ha, Match("Ece", 0.7, {"Ece": 0.7}, 1))
    for _ in range(3):
        await ha.power(185)
        await ha.power(0.5)
    await until(lambda: service.log.totals() == {"Ece": 1})
    await asyncio.sleep(0.1)
    assert len(service.log.recent()) == 1
    await stop()


async def test_bad_token_does_not_crash(fake_ha, caplog):
    _, url = fake_ha
    config = Config(ha_url=url, ha_token="nope")
    async with aiohttp.ClientSession() as session:
        client = HomeAssistant(config, session)
        task = asyncio.create_task(client.run(lambda *a: None, lambda *a: None))
        await until(lambda: "refused the token" in caplog.text)
        assert not client.connected
        task.cancel()


def test_scoreboard_turn_when():
    cfg = Config(window_start="06:30", window_end="08:00")
    service = CoffeeService(cfg, db.Log(":memory:"), None, None)
    tz = ZoneInfo(cfg.timezone)
    service.now = lambda: datetime(2026, 11, 3, 7, 0, tzinfo=tz)
    assert service.scoreboard()["turn_when"] == "today"
    service.log.add(datetime(2026, 11, 3, 6, 45, tzinfo=tz), "Ece", db.FACE)
    board = service.scoreboard()
    assert board["turn_when"] == "tomorrow" and board["turn"] == "Chris"
