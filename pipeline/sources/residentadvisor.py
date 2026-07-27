"""Resident Advisor listings via the public ra.co GraphQL endpoint.

RA is the highest-signal source for this scene: it carries full lineups,
promoter names and venue links that the general ticketing platforms don't.
No API key is required, but the endpoint is undocumented and can change.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

from ..normalize import VANCOUVER, make_event
from . import request_json

ENDPOINT = "https://ra.co/graphql"

# RA's numeric area id for Vancouver. Override with RA_AREA_ID if RA renumbers
# areas -- the ingest logs per-source counts, so a wrong id shows up as zero.
DEFAULT_AREA_ID = 39

NAME = "residentadvisor"
LABEL = "Resident Advisor"

QUERY = (
    "query GET_EVENT_LISTINGS($filters: FilterInputDtoInput, "
    "$filterOptions: FilterOptionsInputDtoInput, $page: Int, $pageSize: Int) {"
    "  eventListings(filters: $filters, filterOptions: $filterOptions, "
    "pageSize: $pageSize, page: $page) {"
    "    data {"
    "      id listingDate"
    "      event {"
    "        id date startTime endTime title contentUrl flyerFront isTicketed"
    # flyerFront comes back empty on listing queries; the artwork actually
    # lives here, and without it an image-led layout has nothing to show.
    "        images { id filename alt type }"
    "        venue { id name contentUrl area { name } }"
    "        promoters { id name }"
    "        artists { id name }"
    "      }"
    "    }"
    # Must be SELECTED, not just passed as an argument -- without this the
    # response carries no facet list and genre enrichment silently never runs.
    "    filterOptions { genre { label value } }"
    "    totalResults"
    "  }"
    "}"
)


def _payload(area_id: int, gte: str, lte: str, page: int, page_size: int = 100, genre: str = ""):
    filters = {
        "areas": {"eq": area_id},
        "listingDate": {"gte": gte, "lte": lte},
    }
    if genre:
        filters["genre"] = {"eq": genre}
    return {
        "operationName": "GET_EVENT_LISTINGS",
        "variables": {
            "filters": filters,
            "filterOptions": {"genre": True},
            "pageSize": page_size,
            "page": page,
        },
        "query": QUERY,
    }


# Enrichment is capped: Vancouver never surfaces more than a handful of genre
# facets, and each one costs a round trip.
MAX_GENRES = 12

IMAGE_HOST = "https://images.ra.co/"


def _artwork(raw: dict) -> str:
    """Best available flyer for a listing.

    RA returns `flyerFront` empty on listing queries, so fall back to the
    `images` array. Entries carry either a bare filename or a full URL.
    """
    candidates = [raw.get("flyerFront") or ""]
    for image in raw.get("images") or []:
        candidates.append((image or {}).get("filename") or "")

    for candidate in candidates:
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        if candidate.startswith("http://") or candidate.startswith("https://"):
            return candidate
        return IMAGE_HOST + candidate.lstrip("/")
    return ""


def _genre_map(area_id: int, gte: str, lte: str, options: list, headers: dict) -> dict:
    """Ask RA which events fall under each genre facet it offers.

    The listing payload carries no per-event genre, so the only way to colour
    the data is to re-run the query once per facet and record what comes back.
    If RA doesn't accept a genre filter, the first call raises and the caller
    keeps the unenriched listings rather than losing them.
    """
    mapping: dict[str, list[str]] = {}
    for option in options[:MAX_GENRES]:
        value = (option or {}).get("value")
        label = ((option or {}).get("label") or value or "").strip().lower()
        if not value or not label:
            continue

        data = request_json(
            ENDPOINT,
            method="POST",
            payload=_payload(area_id, gte, lte, 1, 100, genre=str(value)),
            headers=headers,
            retries=2,
        )
        if data.get("errors"):
            raise RuntimeError(f"genre filter rejected: {data['errors'][:1]}")

        listings = (((data.get("data") or {}).get("eventListings") or {}).get("data")) or []
        for listing in listings:
            event_id = str(((listing.get("event") or {}).get("id")) or "")
            if event_id:
                mapping.setdefault(event_id, []).append(label)
        time.sleep(0.5)
    return mapping


def fetch(days_ahead: int = 120, area_id: int = DEFAULT_AREA_ID) -> list[dict]:
    today = datetime.now(VANCOUVER).date()
    gte = today.isoformat()
    lte = (today + timedelta(days=days_ahead)).isoformat()

    headers = {"Referer": "https://ra.co/events/ca/vancouver", "Origin": "https://ra.co"}

    raw_listings: list[dict] = []
    genre_options: list = []
    page = 1
    while page <= 12:  # 1200 listings is far more than Vancouver ever has queued
        data = request_json(
            ENDPOINT, method="POST", payload=_payload(area_id, gte, lte, page), headers=headers
        )
        if data.get("errors"):
            raise RuntimeError(f"ra.co graphql errors: {data['errors'][:1]}")

        block = ((data.get("data") or {}).get("eventListings")) or {}
        listings = block.get("data") or []
        if not listings:
            break
        if not genre_options:
            genre_options = ((block.get("filterOptions") or {}).get("genre")) or []
        raw_listings.extend(listings)

        page += 1
        time.sleep(1)  # be a good citizen against an undocumented endpoint

    # The listing payload has no per-event genre, so ask RA per facet. Purely
    # additive: if the filter shape is wrong, we keep the plain listings.
    genres_by_id: dict[str, list[str]] = {}
    if genre_options:
        try:
            genres_by_id = _genre_map(area_id, gte, lte, genre_options, headers)
        except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
            print(f"  ra: genre enrichment unavailable ({exc}); using plain listings", file=sys.stderr)
    elif raw_listings:
        # Silence here previously hid the fact that enrichment never ran at all.
        print("  ra: no genre facets in response; every listing keeps the catch-all", file=sys.stderr)

    # Only trust enrichment if it actually covered a meaningful share of the
    # listings -- otherwise a partial map would look like "not electronic".
    coverage = len(genres_by_id) / len(raw_listings) if raw_listings else 0
    trust_genres = coverage >= 0.5
    if genres_by_id and not trust_genres:
        print(f"  ra: genre coverage only {coverage:.0%}; keeping fallback", file=sys.stderr)

    events: list[dict] = []
    for listing in raw_listings:
        raw = listing.get("event") or {}
        venue = raw.get("venue") or {}
        content_url = raw.get("contentUrl") or ""
        url = f"https://ra.co{content_url}" if content_url.startswith("/") else content_url
        promoters = ", ".join(
            p.get("name", "") for p in (raw.get("promoters") or []) if p.get("name")
        )

        tagged = genres_by_id.get(str(raw.get("id") or ""), [])
        if trust_genres and not tagged:
            # RA covers everything programmed at these rooms, including jazz and
            # rock. With genres available, no facet means it isn't a dance night.
            continue

        event = make_event(
            source=NAME,
            source_id=str(raw.get("id") or ""),
            title=raw.get("title"),
            # RA gives a full timestamp in startTime and a date-only fallback.
            start=raw.get("startTime") or raw.get("date"),
            end=raw.get("endTime"),
            venue=venue.get("name") or "",
            address=(venue.get("area") or {}).get("name") or "",
            url=url,
            ticket_url=url if raw.get("isTicketed") else "",
            artists=[a.get("name") for a in (raw.get("artists") or [])],
            genres=tagged,
            image=_artwork(raw),
            promoter=promoters,
            description=f"{promoters} {raw.get('title') or ''}",
            # Few RA titles literally say "house"; without a fallback the best
            # listings on the site would classify to nothing and vanish.
            fallback_genres=["electronic"],
        )
        if event:
            events.append(event)

    return events
