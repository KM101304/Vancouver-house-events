"""Refresh the public event feeds.

Run from the repo root:  python -m pipeline.ingest

Design rule: a source that breaks must never empty the site. Each adapter runs
in isolation; if one fails, its still-upcoming events from the previous run are
carried forward and the failure is recorded in data/meta.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from . import ics
from .normalize import SCHEMA_VERSION, VANCOUVER, dedupe, parse_dt
from .sources import manual, residentadvisor, showpass, ticketmaster

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "data"

# Order matters: earlier sources win a dedupe tie. Curated entries beat the
# platforms because a human wrote them for this scene specifically.
SOURCES = [manual, residentadvisor, ticketmaster, showpass]

# How long a stale event is allowed to live on after its source starts failing.
STALE_GRACE_DAYS = 21


def _load_previous() -> list[dict]:
    path = OUT_DIR / "events.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("events", []) if isinstance(payload, dict) else []
    except (json.JSONDecodeError, OSError):
        return []


def _upcoming(events: list[dict], now: datetime) -> list[dict]:
    """Keep events that haven't finished yet, with a little slack for late sets."""
    cutoff = now - timedelta(hours=6)
    kept = []
    for event in events:
        start = parse_dt(event.get("start"))
        if start and start >= cutoff:
            kept.append(event)
    return kept


def run(days_ahead: int = 120, only: list[str] | None = None) -> dict:
    now = datetime.now(VANCOUVER)
    previous = _load_previous()
    previous_by_source: dict[str, list[dict]] = {}
    for event in previous:
        previous_by_source.setdefault(event.get("source", "?"), []).append(event)

    collected: list[dict] = []
    health: list[dict] = []

    for module in SOURCES:
        name = module.NAME
        if only and name not in only:
            continue

        # A source can opt out cleanly (e.g. Ticketmaster with no API key).
        if hasattr(module, "available") and not module.available():
            health.append({
                "source": name,
                "label": module.LABEL,
                "status": "skipped",
                "count": 0,
                "detail": "no API key configured",
            })
            continue

        try:
            fetched = _upcoming(module.fetch(days_ahead=days_ahead), now)
            collected.extend(fetched)
            health.append({
                "source": name,
                "label": module.LABEL,
                "status": "ok",
                "count": len(fetched),
                "detail": "",
            })
            print(f"  {name:<16} ok      {len(fetched):>4} events", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - one bad feed must not stop the rest
            carried = _upcoming(previous_by_source.get(name, []), now)
            # Don't serve indefinitely stale data if a source stays broken.
            horizon = now + timedelta(days=STALE_GRACE_DAYS)
            carried = [e for e in carried if (parse_dt(e["start"]) or now) <= horizon]
            for event in carried:
                event["stale"] = True
            collected.extend(carried)
            health.append({
                "source": name,
                "label": module.LABEL,
                "status": "failed",
                "count": len(carried),
                "detail": f"{type(exc).__name__}: {exc}"[:300],
            })
            print(f"  {name:<16} FAILED  carried {len(carried)} — {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    events = dedupe(collected, [m.NAME for m in SOURCES])

    venues: dict[str, dict] = {}
    for event in events:
        key = event["venue"]
        if not key:
            continue
        entry = venues.setdefault(key, {"name": key, "count": 0, "genres": {}, "area": event.get("address", "")})
        entry["count"] += 1
        for genre in event["genres"]:
            entry["genres"][genre] = entry["genres"].get(genre, 0) + 1

    venue_list = sorted(
        (
            {
                "name": v["name"],
                "count": v["count"],
                "area": v["area"],
                # The genres this room actually books, most common first.
                "genres": sorted(v["genres"], key=lambda g: -v["genres"][g])[:3],
            }
            for v in venues.values()
        ),
        key=lambda v: (-v["count"], v["name"]),
    )

    genre_counts: dict[str, int] = {}
    for event in events:
        for genre in event["genres"]:
            genre_counts[genre] = genre_counts.get(genre, 0) + 1

    meta = {
        "schemaVersion": SCHEMA_VERSION,
        "updated": now.isoformat(),
        "eventCount": len(events),
        "venueCount": len(venue_list),
        "windowDays": days_ahead,
        "sources": health,
        "genres": dict(sorted(genre_counts.items(), key=lambda kv: -kv[1])),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _write(OUT_DIR / "events.json", json.dumps({"meta": meta, "events": events}, indent=1, ensure_ascii=False))
    _write(OUT_DIR / "venues.json", json.dumps({"updated": meta["updated"], "venues": venue_list}, indent=1, ensure_ascii=False))
    _write(OUT_DIR / "meta.json", json.dumps(meta, indent=1, ensure_ascii=False))
    _write(OUT_DIR / "events.ics", ics.build(events))

    return meta


def _write(path: Path, content: str) -> None:
    path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Vancouver house event feeds")
    parser.add_argument("--days", type=int, default=120, help="how far ahead to look")
    parser.add_argument("--only", nargs="*", help="restrict to named sources")
    args = parser.parse_args()

    print("Refreshing feeds…", file=sys.stderr)
    meta = run(days_ahead=args.days, only=args.only)

    ok = [s for s in meta["sources"] if s["status"] == "ok"]
    failed = [s for s in meta["sources"] if s["status"] == "failed"]
    print(
        f"\n{meta['eventCount']} events across {meta['venueCount']} venues "
        f"({len(ok)} sources ok, {len(failed)} failed)",
        file=sys.stderr,
    )

    # Only a total wipeout is worth failing the job over -- partial data is
    # still a working site, and CI noise trains people to ignore CI.
    if meta["eventCount"] == 0 and failed:
        print("error: no events and every live source failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
