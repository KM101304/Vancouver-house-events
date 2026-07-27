"""Source adapters. Each module exposes ``fetch() -> list[dict]`` of raw events.

Adapters are deliberately dumb: they talk to one platform, hand back normalized
events, and raise on failure. The orchestrator decides what a failure means.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

TIMEOUT = 30
RETRIES = 3


def request_json(url: str, *, method: str = "GET", payload=None, headers=None, retries: int = RETRIES):
    """HTTP with retry/backoff, returning parsed JSON."""
    body = None
    hdrs = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-CA,en;q=0.9",
        "Accept-Encoding": "gzip",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)

    last_error = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001 - retry everything, report at the end
            last_error = exc
            status = getattr(exc, "code", None)
            # 4xx other than rate limiting won't fix itself on retry.
            if isinstance(exc, urllib.error.HTTPError) and status not in (408, 429) and 400 <= status < 500:
                break
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"request failed for {url}: {last_error}") from last_error


def qs(base: str, params: dict) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    return f"{base}?{urllib.parse.urlencode(clean)}"
