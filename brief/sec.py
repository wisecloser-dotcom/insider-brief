"""Polite, cached HTTP client for SEC EDGAR.

Archive files (a filed Form 4, a past daily index) never change, so they are cached
forever. Company filing lists change, so they are refreshed after a few hours.
"""
import hashlib
import json
import time
from pathlib import Path

import requests


class SecClient:
    def __init__(self, user_agent: str, cache_dir: Path, max_rps: float = 8.0):
        if "example.com" in user_agent or "@" not in user_agent:
            raise SystemExit(
                'The SEC needs a contact: add --sec-agent "Your Name you@email.com" '
                "(or set sec_user_agent in brief/config.py)."
            )
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.gap = 1.0 / max_rps
        self._last = 0.0
        self.cache = Path(cache_dir) / "sec"
        self.cache.mkdir(parents=True, exist_ok=True)

    def _path(self, url: str) -> Path:
        return self.cache / hashlib.sha1(url.encode()).hexdigest()

    def get_bytes(self, url: str, max_age_hours: float | None = None) -> bytes:
        """max_age_hours=None caches forever."""
        p = self._path(url)
        if p.exists() and (max_age_hours is None
                           or time.time() - p.stat().st_mtime < max_age_hours * 3600):
            return p.read_bytes()
        for attempt in range(4):
            wait = self.gap - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            r = self.s.get(url, timeout=60)
            if r.status_code == 200:
                p.write_bytes(r.content)
                return r.content
            if r.status_code in (403, 404):
                r.raise_for_status()
            time.sleep(2 ** attempt * 2)   # 429 / 5xx: back off and retry
        r.raise_for_status()
        return b""

    def get_text(self, url, max_age_hours=None) -> str:
        return self.get_bytes(url, max_age_hours).decode("utf-8", errors="replace")

    def get_json(self, url, max_age_hours=None) -> dict:
        return json.loads(self.get_bytes(url, max_age_hours))
