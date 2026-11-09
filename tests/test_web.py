"""The website can answer for a coffee the camera couldn't place, and nothing else: no ticks by hand."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app import db, web as website
from app.config import Config
from app.service import CoffeeService

TZ = ZoneInfo("Europe/London")


@pytest.fixture
async def site():
    service = CoffeeService(Config(), db.Log(":memory:"), None, None)
    client = TestClient(TestServer(website.build(service)))
    await client.start_server()
    yield service, client
    await client.close()


async def test_no_adding_ticks_by_hand(site):
    service, client = site
    r = await client.post("/api/brews", json={"person": "Chris"})
    assert r.status in (404, 405)
    assert service.log.totals() == {}


async def test_camera_ticks_cannot_be_changed_or_deleted(site):
    service, client = site
    brew = service.log.add(datetime(2026, 11, 9, 7, 30, tzinfo=TZ), "Ece", db.FACE, 0.6)
    r = await client.patch(f"/api/brews/{brew.id}", json={"person": "Chris"})
    assert r.status == 409
    r = await client.delete(f"/api/brews/{brew.id}")
    assert r.status == 409
    assert service.log.get(brew.id).person == "Ece"


async def test_answering_an_unplaced_coffee(site):
    service, client = site
    brew = service.log.add(datetime(2026, 11, 9, 7, 30, tzinfo=TZ), None, db.PENDING)
    r = await client.patch(f"/api/brews/{brew.id}", json={"person": "Chris"})
    assert r.status == 200
    assert service.log.get(brew.id).person == "Chris"
    assert service.log.get(brew.id).source == db.MANUAL
    r = await client.patch(f"/api/brews/{brew.id}", json={"person": "Ece"})
    assert r.status == 409  # answered once, now it's settled


async def test_not_coffee_for_an_unplaced_coffee(site):
    service, client = site
    brew = service.log.add(datetime(2026, 11, 9, 7, 30, tzinfo=TZ), None, db.PENDING)
    r = await client.delete(f"/api/brews/{brew.id}")
    assert r.status == 204
    assert service.log.get(brew.id) is None
    r = await client.delete("/api/brews/999")
    assert r.status == 404
