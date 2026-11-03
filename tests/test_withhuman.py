"""Asking through withHuman, against a fake AAP provider that speaks the real request shapes."""

import asyncio
import uuid
from datetime import datetime, timezone

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app import db
from app.config import Config
from app.detector import Grind
from app.service import CoffeeService
from app.withhuman import WithHuman

TERMINAL = {"approved", "denied", "expired", "cancelled"}


class FakeAAP:
    """Requests stay pending until a test decides them, the way a reviewer would."""

    def __init__(self, token="good"):
        self.token = token
        self.requests: dict[str, dict] = {}
        self.changed: dict[str, asyncio.Event] = {}
        self.created_with: list[tuple[dict, dict]] = []  # (headers, body)
        self.consumed: list[str] = []
        self.app = web.Application()
        base = "/api/aap/v1/requests"
        self.app.add_routes([
            web.post(base, self.create),
            web.get(base + "/{id}", self.get),
            web.delete(base + "/{id}", self.cancel),
            web.post(base + "/{id}/consume", self.consume),
        ])

    def _authorised(self, request) -> bool:
        return request.headers.get("Authorization") == f"Bearer {self.token}"

    async def create(self, request):
        if not self._authorised(request):
            return web.json_response({"error": {"code": "unauthorized"}}, status=401)
        body = await request.json()
        self.created_with.append((dict(request.headers), body))
        rid = str(uuid.uuid4())
        self.requests[rid] = body | {"id": rid, "status": "pending"}
        self.changed[rid] = asyncio.Event()
        return web.json_response(self.requests[rid], status=201)

    async def get(self, request):
        rid = request.match_info["id"]
        if rid not in self.requests:
            return web.json_response({}, status=404)
        if self.requests[rid]["status"] == "pending" and request.query.get("wait"):
            try:
                await asyncio.wait_for(self.changed[rid].wait(), 0.2)  # "30s", sped up
            except asyncio.TimeoutError:
                pass
        return web.json_response(self.requests[rid])

    async def cancel(self, request):
        rid = request.match_info["id"]
        if self.requests[rid]["status"] in TERMINAL:
            return web.json_response({"error": {"code": "already_terminal"}}, status=409)
        self.decide_id(rid, "cancelled")
        return web.json_response(self.requests[rid])

    async def consume(self, request):
        rid = request.match_info["id"]
        if self.requests[rid]["status"] != "approved":
            return web.json_response({"error": {"code": "not_approved"}}, status=409)
        if rid in self.consumed:
            return web.json_response({"error": {"code": "already_consumed"}}, status=409)
        self.consumed.append(rid)
        return web.json_response(self.requests[rid])

    def for_person(self, person) -> str:
        return next(rid for rid, r in self.requests.items() if r["arguments"]["person"] == person)

    def decide(self, person, status):
        self.decide_id(self.for_person(person), status)

    def decide_id(self, rid, status):
        self.requests[rid]["status"] = status
        self.changed[rid].set()

    def status(self, person) -> str:
        return self.requests[self.for_person(person)]["status"]


async def until(check, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition never became true")


@pytest.fixture
async def aap():
    fake = FakeAAP()
    server = TestServer(fake.app)
    await server.start_server()
    session = aiohttp.ClientSession()

    def service(log=None, token="good"):
        cfg = Config(withhuman_url=f"http://{server.host}:{server.port}", withhuman_token=token)
        return CoffeeService(cfg, log or db.Log(":memory:"), None, None, WithHuman(cfg, session))

    yield fake, service
    await session.close()
    await server.close()


async def grind(svc) -> db.Brew:
    await svc.on_grind(Grind(datetime(2026, 11, 3, 7, 12, tzinfo=timezone.utc), new_round=True))
    return svc.log.recent()[0]


async def test_asks_each_person_their_own_question(aap):
    fake, service = aap
    svc = service()
    brew = await grind(svc)
    assert [body["arguments"]["person"] for _, body in fake.created_with] == ["Ece", "Chris"]
    headers, body = fake.created_with[0]
    assert body["tool"] == "claim_coffee" and body["timeout"] == "12h"
    assert body["arguments"]["question"] == "Did you make the 07:12 coffee?"
    assert headers["Idempotency-Key"] == f"coffee-{brew.id}-Ece"
    assert "Approve if you made it" in body["agent_reasoning"]


async def test_approval_ticks_that_person_and_withdraws_the_other(aap):
    fake, service = aap
    svc = service()
    brew = await grind(svc)
    fake.decide("Chris", "approved")
    await until(lambda: svc.log.totals() == {"Chris": 1})
    assert svc.log.get(brew.id).source == db.PHONE
    assert fake.consumed == [fake.for_person("Chris")]
    await until(lambda: fake.status("Ece") == "cancelled")


async def test_both_denying_means_not_coffee(aap):
    fake, service = aap
    svc = service()
    brew = await grind(svc)
    fake.decide("Ece", "denied")
    await asyncio.sleep(0.1)
    assert svc.log.get(brew.id) is not None  # Chris might still say yes
    fake.decide("Chris", "denied")
    await until(lambda: svc.log.get(brew.id) is None)


async def test_expired_stays_pending_for_the_website(aap):
    fake, service = aap
    svc = service()
    brew = await grind(svc)
    fake.decide("Ece", "expired")
    fake.decide("Chris", "expired")
    await until(lambda: all(q.answer == "expired" for q in svc.log.questions(brew.id)))
    assert svc.log.pending()[0].id == brew.id


async def test_answering_on_the_website_withdraws_both(aap):
    fake, service = aap
    svc = service()
    brew = await grind(svc)
    svc.log.set_person(brew.id, "Ece", db.MANUAL)
    await svc.clear_question(brew.id)
    assert fake.status("Ece") == fake.status("Chris") == "cancelled"
    await asyncio.sleep(0.1)
    assert svc.log.get(brew.id).source == db.MANUAL


async def test_keeps_waiting_after_a_restart(aap, tmp_path):
    fake, service = aap
    first = service(db.Log(tmp_path / "coffee.db"))
    brew = await grind(first)
    for task in list(first._tasks):
        task.cancel()  # the add-on restarts

    again = service(db.Log(tmp_path / "coffee.db"))
    again.resume()
    fake.decide("Ece", "approved")
    await until(lambda: again.log.totals() == {"Ece": 1})
    assert again.log.get(brew.id).person == "Ece"


async def test_bad_key_leaves_it_pending(aap, caplog):
    fake, service = aap
    svc = service(token="revoked")
    brew = await grind(svc)
    assert "withHuman refused the question" in caplog.text
    assert svc.log.questions(brew.id) == [] and svc.log.pending()[0].id == brew.id
