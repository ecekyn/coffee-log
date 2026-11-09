"""Small Home Assistant client: watch one sensor, hear notification taps, grab camera frames, send notifications, speak."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import aiohttp

from .config import Config

log = logging.getLogger(__name__)

NOTIFICATION_ACTION = "mobile_app_notification_action"


class HomeAssistant:
    def __init__(self, cfg: Config, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session
        self.connected = False

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.cfg.token}"}

    async def run(
        self,
        on_state: Callable[[str | None, bool], Awaitable[None]],
        on_action: Callable[[str], Awaitable[None]],
    ) -> None:
        """Stay subscribed forever, reconnecting with backoff.

        on_state(state, initial) gets every new state of the power sensor; initial=True for the
        reading taken right after connecting. on_action(action_id) gets notification button taps.
        """
        delay = 1
        while True:
            try:
                await self._session_once(on_state, on_action)
                delay = 1
            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
                log.warning("Home Assistant connection lost: %s", e)
            except AuthError as e:
                log.error("%s. Check ha_token.", e)
                delay = 60
            self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _session_once(self, on_state, on_action) -> None:
        async with self.session.ws_connect(self.cfg.ws_url, heartbeat=30) as ws:
            await ws.receive_json()  # auth_required
            await ws.send_json({"type": "auth", "access_token": self.cfg.token})
            reply = await ws.receive_json()
            if reply.get("type") != "auth_ok":
                raise AuthError(f"Home Assistant refused the token ({reply.get('message', reply.get('type'))})")

            await ws.send_json({
                "id": 1,
                "type": "subscribe_trigger",
                "trigger": {"platform": "state", "entity_id": self.cfg.power_sensor},
            })
            await ws.send_json({"id": 2, "type": "subscribe_events", "event_type": NOTIFICATION_ACTION})
            self.connected = True
            log.info("Connected to Home Assistant, watching %s", self.cfg.power_sensor)

            await on_state(await self.state(self.cfg.power_sensor), True)

            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                data = msg.json()
                if data.get("type") == "result" and not data.get("success"):
                    log.error("Home Assistant rejected subscription %s: %s", data.get("id"), data.get("error"))
                if data.get("type") != "event":
                    continue
                event = data["event"]
                if data["id"] == 1:
                    to_state = event["variables"]["trigger"].get("to_state") or {}
                    await on_state(to_state.get("state"), False)
                elif data["id"] == 2:
                    action = event.get("data", {}).get("action")
                    if action:
                        await on_action(action)

    async def state(self, entity_id: str) -> str | None:
        async with self.session.get(f"{self.cfg.api_url}/states/{entity_id}", headers=self._headers) as r:
            if r.status == 404:
                log.error("Entity %s not found in Home Assistant", entity_id)
                return None
            r.raise_for_status()
            return (await r.json()).get("state")

    async def frames(self, count: int, interval_s: float) -> list[bytes]:
        """Live JPEG frames, interval_s apart, from one connection to the camera's stream.

        Not /camera_proxy: TP-Link's integration caches those stills for five minutes, so they
        show whoever was there earlier. The stream takes a couple of seconds to start.
        """
        url = f"{self.cfg.api_url}/camera_proxy_stream/{self.cfg.camera_entity}"
        timeout = aiohttp.ClientTimeout(total=15 + count * interval_s)
        frames: list[bytes] = []
        buffer = b""
        next_at = 0.0
        try:
            async with self.session.get(url, headers=self._headers, timeout=timeout) as r:
                r.raise_for_status()
                async for chunk in r.content.iter_any():
                    buffer += chunk
                    while (found := _next_jpeg(buffer))[0]:
                        jpeg, buffer = found
                        if time.monotonic() >= next_at:
                            frames.append(jpeg)
                            next_at = time.monotonic() + interval_s
                            if len(frames) == count:
                                return frames
                    buffer = found[1]
        except asyncio.TimeoutError:
            if not frames:
                raise
        if not frames:
            raise ConnectionError("the camera stream ended without a frame")
        return frames

    async def tts_url(self, engine: str, message: str) -> str:
        """A URL Home Assistant serves the spoken message from (an MP3, no token needed to fetch it)."""
        body = {"engine_id": engine, "message": message}
        async with self.session.post(f"{self.cfg.api_url}/tts_get_url", headers=self._headers, json=body) as r:
            if r.status >= 400:
                raise ConnectionError(f"text-to-speech failed: {r.status} {await r.text()}")
            path = (await r.json())["path"]
        return self.cfg.ha_url.rstrip("/") + path

    async def notify(self, service: str, message: str, data: dict | None = None, title: str | None = None) -> None:
        domain, _, name = service.partition(".")
        body = {"message": message}
        if title:
            body["title"] = title
        if data:
            body["data"] = data
        async with self.session.post(f"{self.cfg.api_url}/services/{domain}/{name}", headers=self._headers, json=body) as r:
            if r.status >= 400:
                log.error("Calling %s failed: %s %s", service, r.status, await r.text())


def _next_jpeg(buffer: bytes) -> tuple[bytes | None, bytes]:
    """The first complete JPEG in buffer, and what's left to keep reading from."""
    start = buffer.find(b"\xff\xd8")
    if start < 0:
        return None, b""
    end = buffer.find(b"\xff\xd9", start + 2)
    if end < 0:
        return None, buffer[start:]
    return buffer[start:end + 2], buffer[end + 2:]


class AuthError(Exception):
    pass
