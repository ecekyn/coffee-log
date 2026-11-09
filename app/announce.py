"""Say who got the tick, out of the kitchen camera's speaker.

Home Assistant turns the text into an MP3; go2rtc plays it through the camera's two-way audio.
"""

import logging

import aiohttp

from .config import Config
from .ha import HomeAssistant

log = logging.getLogger(__name__)


class Announcer:
    def __init__(self, cfg: Config, session: aiohttp.ClientSession, ha: HomeAssistant):
        self.cfg = cfg
        self.session = session
        self.ha = ha

    def text(self, person: str) -> str:
        return self.cfg.announce_text.format(name=self.cfg.spoken_names.get(person, person))

    async def say(self, person: str) -> None:
        """Best effort: a quiet camera must never cost anyone their tick."""
        try:
            audio = await self.ha.tts_url(self.cfg.tts_engine, self.text(person))
            # Tapo cameras only take 8 kHz A-law, so go2rtc's ffmpeg converts the MP3 on the way.
            src = f"ffmpeg:{audio}#audio=pcma#input=file"
            url = self.cfg.announce_go2rtc_url.rstrip("/") + "/api/streams"
            params = {"dst": self.cfg.announce_stream, "src": src}
            async with self.session.post(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status >= 400:
                    log.warning("Couldn't announce %s: go2rtc said %s %s", person, r.status, await r.text())
                    return
            log.info("Announced %s on %s", person, self.cfg.announce_stream)
        except Exception as e:
            log.warning("Couldn't announce %s: %s", person, e)
