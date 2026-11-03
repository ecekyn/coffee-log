"""Ask "did you make this coffee?" through withHuman, over the Agent Approval Protocol (AAP).

Each person gets their own approval request, and the withHuman pipeline sends it to that person
only, so approving means "I made it" and denying means "not me". A decision doesn't say who made
it; the routing does.
"""

import asyncio
import logging

import aiohttp

from .config import Config

log = logging.getLogger(__name__)

TOOL = "claim_coffee"
PENDING = "pending"
CANCELLED = "cancelled"


class WithHuman:
    def __init__(self, cfg: Config, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session
        self.base = cfg.withhuman_url.rstrip("/") + "/api/aap/v1"

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.cfg.withhuman_token}"}

    async def ask(self, brew_id: int, person: str, arguments: dict, reasoning: str) -> dict | None:
        """Create one person's request. Returns it (maybe already decided), or None if withHuman said no."""
        body = {
            "tool": TOOL,
            "arguments": {"person": person, **arguments},
            "agent_reasoning": reasoning,
            "context": {"app": "coffee-log", "brew_id": brew_id},
            "timeout": self.cfg.withhuman_timeout,
        }
        headers = self._headers | {"Idempotency-Key": f"coffee-{brew_id}-{person}"}
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(2 ** attempt)
            try:
                async with self.session.post(f"{self.base}/requests", headers=headers, json=body) as r:
                    if r.status == 201:
                        return await r.json()
                    log.error("withHuman refused the question for %s: %s %s", person, r.status, await r.text())
                    if r.status < 500:
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning("Couldn't reach withHuman: %s", e)
        return None

    async def wait(self, request_id: str) -> str:
        """Long-poll until the request is approved, denied, expired or cancelled; returns that status."""
        delay = 1
        while True:
            try:
                async with self.session.get(
                    f"{self.base}/requests/{request_id}",
                    headers=self._headers,
                    params={"wait": "30s"},
                    timeout=aiohttp.ClientTimeout(total=45),
                ) as r:
                    if r.status == 200:
                        status = (await r.json())["status"]
                        if status != PENDING:
                            return status
                        delay = 1
                        continue
                    if r.status in (401, 403, 404):
                        # Credential revoked, agent archived, or the request is gone: it won't get better.
                        log.error("withHuman stopped answering about %s: %s %s", request_id, r.status, await r.text())
                        return CANCELLED
                    log.warning("withHuman answered %s while waiting on %s", r.status, request_id)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning("Lost withHuman while waiting on %s: %s", request_id, e)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def consume(self, request_id: str) -> bool:
        """Use the approval. Only one consume succeeds, so a double tap can't tick twice."""
        headers = self._headers | {"Idempotency-Key": f"coffee-consume-{request_id}"}
        try:
            async with self.session.post(f"{self.base}/requests/{request_id}/consume", headers=headers, json={}) as r:
                if r.status == 200:
                    return True
                log.warning("withHuman wouldn't let %s be used: %s %s", request_id, r.status, await r.text())
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Couldn't reach withHuman to use %s: %s", request_id, e)
        return False

    async def cancel(self, request_id: str) -> None:
        """Withdraw a question nobody needs to answer any more. Already decided is fine."""
        try:
            async with self.session.delete(f"{self.base}/requests/{request_id}", headers=self._headers) as r:
                if r.status not in (200, 404, 405, 409):
                    log.warning("Cancelling %s on withHuman failed: %s %s", request_id, r.status, await r.text())
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Couldn't reach withHuman to cancel %s: %s", request_id, e)
