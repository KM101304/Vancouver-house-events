"""Hand-curated listings from data/manual-events.json.

A large share of Vancouver's house scene never touches a ticketing API --
Instagram-announced warehouse parties, afterhours, door-only nights. This
source is how those get onto the site, and it always wins during dedupe.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..normalize import make_event

NAME = "manual"
LABEL = "Curated"

DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "manual-events.json"


def fetch(days_ahead: int = 365) -> list[dict]:
    if not DATA_FILE.exists():
        return []

    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    entries = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise RuntimeError("manual-events.json: expected an 'events' array")

    events = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict) or raw.get("draft"):
            continue
        event = make_event(
            source=NAME,
            source_id=raw.get("id") or f"manual-{index}",
            title=raw.get("title"),
            start=raw.get("start"),
            end=raw.get("end"),
            venue=raw.get("venue") or "",
            address=raw.get("address") or "",
            url=raw.get("url") or "",
            ticket_url=raw.get("ticketUrl") or "",
            artists=raw.get("artists") or [],
            genres=raw.get("genres") or [],
            image=raw.get("image") or "",
            promoter=raw.get("promoter") or "",
            price_min=raw.get("priceMin"),
            price_max=raw.get("priceMax"),
            description=raw.get("description") or "",
            # Curated entries are trusted: if a human added it, it belongs here
            # even when the title gives the classifier nothing to work with.
            fallback_genres=["electronic"],
        )
        if event:
            events.append(event)
    return events
