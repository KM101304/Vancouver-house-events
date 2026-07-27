"""Resident Advisor listings via the public ra.co GraphQL endpoint.

RA is the highest-signal source for this scene: it carries full lineups,
promoter names and venue links that the general ticketing platforms don't.
No API key is required, but the endpoint is undocumented and can change.
"""

from __future__ import annotations

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
    "        venue { id name contentUrl area { name } }"
    "        promoters { id name }"
    "        artists { id name }"
    "      }"
    "    }"
    "    totalResults"
    "  }"
    "}"
)


def _payload(area_id: int, gte: str, lte: str, page: int, page_size: int = 100):
    return {
        "operationName": "GET_EVENT_LISTINGS",
        "variables": {
            "filters": {
                "areas": {"eq": area_id},
                "listingDate": {"gte": gte, "lte": lte},
            },
            "filterOptions": {"genre": True},
            "pageSize": page_size,
            "page": page,
        },
        "query": QUERY,
    }


def fetch(days_ahead: int = 120, area_id: int = DEFAULT_AREA_ID) -> list[dict]:
    today = datetime.now(VANCOUVER).date()
    gte = today.isoformat()
    lte = (today + timedelta(days=days_ahead)).isoformat()

    headers = {"Referer": "https://ra.co/events/ca/vancouver", "Origin": "https://ra.co"}

    events: list[dict] = []
    page = 1
    while page <= 12:  # 1200 listings is far more than Vancouver ever has queued
        data = request_json(
            ENDPOINT, method="POST", payload=_payload(area_id, gte, lte, page), headers=headers
        )
        if data.get("errors"):
            raise RuntimeError(f"ra.co graphql errors: {data['errors'][:1]}")

        listings = (((data.get("data") or {}).get("eventListings") or {}).get("data")) or []
        if not listings:
            break

        for listing in listings:
            raw = listing.get("event") or {}
            venue = raw.get("venue") or {}
            content_url = raw.get("contentUrl") or ""
            url = f"https://ra.co{content_url}" if content_url.startswith("/") else content_url
            promoters = ", ".join(
                p.get("name", "") for p in (raw.get("promoters") or []) if p.get("name")
            )

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
                image=raw.get("flyerFront") or "",
                promoter=promoters,
                description=f"{promoters} {raw.get('title') or ''}",
                # Everything RA lists is electronic, but few titles literally say
                # "house". Without this, keyword classification drops most of the
                # best listings on the site.
                fallback_genres=["electronic"],
            )
            if event:
                events.append(event)

        page += 1
        time.sleep(1)  # be a good citizen against an undocumented endpoint

    return events
