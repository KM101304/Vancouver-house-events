"""Adapter tests against recorded response shapes.

The live endpoints can't be reached from every environment (and RA's is
undocumented), so each adapter is exercised against a fixture matching the
payload shape it was written for. This catches field-mapping mistakes -- the
most likely way an adapter breaks -- without needing network access.

Run: python -m pipeline.test_sources
"""

from __future__ import annotations

import sys

from .sources import residentadvisor, showpass, ticketmaster

FAILURES: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual != expected:
        FAILURES.append(f"{label}\n    expected: {expected!r}\n    actual:   {actual!r}")


def check_true(label: str, value) -> None:
    if not value:
        FAILURES.append(f"{label}\n    expected truthy, got {value!r}")


class Patch:
    """Swap a module attribute for the duration of a block."""

    def __init__(self, module, name, value):
        self.module, self.name, self.value = module, name, value

    def __enter__(self):
        self.original = getattr(self.module, self.name)
        setattr(self.module, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.module, self.name, self.original)


# --------------------------------------------------------------- fixtures

RA_EVENT = {
    "id": "1",
    "listingDate": "2026-08-15T00:00:00.000",
    "event": {
        "id": "2049821",
        "date": "2026-08-15T00:00:00.000",
        "startTime": "2026-08-15T23:00:00.000",
        "endTime": "2026-08-16T03:00:00.000",
        "title": "Pacific Rhythm presents: Basement",
        "contentUrl": "/events/2049821",
        "flyerFront": "https://images.ra.co/flyer.jpg",
        "isTicketed": True,
        "venue": {"id": "9", "name": "Open Studios", "contentUrl": "/clubs/9",
                  "area": {"name": "Vancouver"}},
        "promoters": [{"id": "3", "name": "Pacific Rhythm"}],
        "artists": [{"id": "4", "name": "Nocturne"}, {"id": "5", "name": "Low Tide"}],
    },
}

TM_EVENT = {
    "id": "G5vYZbLkQ",
    "name": "Techno All Night",
    "url": "https://www.ticketmaster.ca/event/G5vYZbLkQ",
    "images": [
        {"url": "https://s1.ticketm.net/small.jpg", "width": 305},
        {"url": "https://s1.ticketm.net/large.jpg", "width": 2048},
    ],
    "dates": {"start": {"localDate": "2026-08-20", "localTime": "22:00:00",
                        "dateTime": "2026-08-21T05:00:00Z"}},
    "classifications": [{"genre": {"name": "Dance/Electronic"},
                         "subGenre": {"name": "Techno"}}],
    "priceRanges": [{"type": "standard", "currency": "CAD", "min": 25.0, "max": 45.0}],
    "_embedded": {
        "venues": [{"name": "Harbour Event Centre", "address": {"line1": "750 Pacific Blvd"}}],
        "attractions": [{"name": "Headline Act"}],
    },
}

SHOWPASS_EVENT = {
    "id": 88213,
    "name": "Deep House Sundays",
    "slug": "deep-house-sundays",
    "starts_on": "2026-08-16T21:00:00",
    "ends_on": "2026-08-17T02:00:00",
    "venue": {"name": "The Pearl", "address": "1379 Granville St"},
    "tags": [{"name": "House"}],
    "description": "Deep house all night.",
    "image": "https://showpass.com/img.jpg",
}


def _paged(*pages):
    """Return a request_json stand-in that serves fixture pages in order."""
    calls = {"n": 0}

    def fake(*args, **kwargs):
        index = calls["n"]
        calls["n"] += 1
        return pages[index] if index < len(pages) else pages[-1]

    fake.calls = calls
    return fake


# ------------------------------------------------------------------ tests


def test_residentadvisor() -> None:
    page1 = {"data": {"eventListings": {"data": [RA_EVENT], "totalResults": 1}}}
    empty = {"data": {"eventListings": {"data": [], "totalResults": 1}}}
    fake = _paged(page1, empty)

    with Patch(residentadvisor, "request_json", fake), Patch(residentadvisor.time, "sleep", lambda s: None):
        events = residentadvisor.fetch(days_ahead=30)

    check("ra returns one event", len(events), 1)
    if not events:
        return
    event = events[0]
    check("ra title", event["title"], "Pacific Rhythm presents: Basement")
    check("ra venue", event["venue"], "Open Studios")
    # contentUrl is relative and must be absolutized.
    check("ra url absolutized", event["url"], "https://ra.co/events/2049821")
    check("ra ticket url set when ticketed", event["ticketUrl"], "https://ra.co/events/2049821")
    check("ra artists", event["artists"], ["Nocturne", "Low Tide"])
    check("ra promoter", event["promoter"], "Pacific Rhythm")
    check("ra image", event["image"], "https://images.ra.co/flyer.jpg")
    check("ra source", event["source"], "residentadvisor")
    # startTime carries the real clock time; date alone would give 22:00.
    check("ra uses startTime", event["start"][11:16], "23:00")
    check("ra night", event["night"], "2026-08-15")
    # Nothing in the title says "techno", so the fallback genre must apply.
    check("ra fallback genre", event["genres"], ["electronic"])

    # Pagination stops on the empty page rather than looping to the cap.
    check("ra stopped after empty page", fake.calls["n"], 2)


def test_residentadvisor_query_selects_filter_options() -> None:
    """Regression: filterOptions must be selected, not only passed as an arg.

    It was previously only an argument, so the response never carried a facet
    list and genre enrichment was skipped on every single run.
    """
    check_true("query selects filterOptions", "filterOptions {" in residentadvisor.QUERY)
    check_true("query selects genre value", "genre { label value }" in residentadvisor.QUERY)


def _ra_page(events, genre_options=None):
    block = {"data": events, "totalResults": len(events)}
    if genre_options is not None:
        block["filterOptions"] = {"genre": genre_options}
    return {"data": {"eventListings": block}}


def _ra_jazz():
    raw = {"id": "7", "listingDate": "2026-08-16T00:00:00.000", "event": dict(RA_EVENT["event"])}
    raw["event"] = dict(RA_EVENT["event"], id="99", title="Some Jazz Trio Live")
    return raw


def test_residentadvisor_genre_enrichment() -> None:
    """Real genres replace the fallback, and untagged listings are dropped."""
    options = [{"label": "House", "value": "8"}, {"label": "Techno", "value": "13"}]
    empty = _ra_page([])
    pages = [
        _ra_page([RA_EVENT, _ra_jazz()], options),  # base page 1
        empty,                                      # base page 2 -> stop
        _ra_page([RA_EVENT]),                       # genre "House" matches the techno fixture id
        _ra_page([]),                               # genre "Techno" matches nothing
    ]
    with Patch(residentadvisor, "request_json", _paged(*pages)), \
            Patch(residentadvisor.time, "sleep", lambda s: None):
        events = residentadvisor.fetch(days_ahead=30)

    check("enriched genre applied", events[0]["genres"] if events else None, ["house"])
    # The jazz listing carried no facet, so with genres available it isn't a
    # dance night and must not reach the site.
    check("untagged listing dropped", len(events), 1)


def test_residentadvisor_survives_rejected_genre_filter() -> None:
    """If RA won't accept a genre filter, listings must still come through."""
    options = [{"label": "House", "value": "8"}]
    pages = [
        _ra_page([RA_EVENT], options),
        _ra_page([]),
        {"errors": [{"message": "Unknown argument 'genre'"}]},
    ]
    with Patch(residentadvisor, "request_json", _paged(*pages)), \
            Patch(residentadvisor.time, "sleep", lambda s: None):
        events = residentadvisor.fetch(days_ahead=30)

    check("listings survive a rejected genre filter", len(events), 1)
    check("falls back to catch-all", events[0]["genres"] if events else None, ["electronic"])


def test_residentadvisor_ignores_thin_genre_coverage() -> None:
    """Partial enrichment must not be read as 'these events aren't dance'."""
    options = [{"label": "House", "value": "8"}]
    many = [dict(RA_EVENT, event=dict(RA_EVENT["event"], id=str(i))) for i in range(10)]
    pages = [
        _ra_page(many, options),
        _ra_page([]),
        _ra_page(many[:1]),   # only 1 of 10 tagged -> 10% coverage
    ]
    with Patch(residentadvisor, "request_json", _paged(*pages)), \
            Patch(residentadvisor.time, "sleep", lambda s: None):
        events = residentadvisor.fetch(days_ahead=30)

    check("thin coverage keeps every listing", len(events), 10)


def test_residentadvisor_surfaces_graphql_errors() -> None:
    bad = {"errors": [{"message": "Cannot query field 'promoters'"}]}
    with Patch(residentadvisor, "request_json", _paged(bad)), Patch(residentadvisor.time, "sleep", lambda s: None):
        try:
            residentadvisor.fetch(days_ahead=30)
            FAILURES.append("ra should raise on graphql errors, returned normally")
        except RuntimeError as exc:
            check_true("ra error mentions graphql", "graphql" in str(exc).lower())


def test_ticketmaster() -> None:
    page = {"_embedded": {"events": [TM_EVENT]}, "page": {"totalPages": 1}}
    with Patch(ticketmaster, "request_json", _paged(page)), Patch(ticketmaster.time, "sleep", lambda s: None):
        with Patch(ticketmaster.os, "environ", {"TICKETMASTER_API_KEY": "test-key"}):
            events = ticketmaster.fetch(days_ahead=30)

    check("tm returns one event", len(events), 1)
    if not events:
        return
    event = events[0]
    check("tm title", event["title"], "Techno All Night")
    check("tm venue", event["venue"], "Harbour Event Centre")
    check("tm address", event["address"], "750 Pacific Blvd")
    # dateTime is UTC; 05:00Z is 22:00 the previous evening in Vancouver.
    check("tm converts utc to local", event["start"][:16], "2026-08-20T22:00")
    check("tm picks largest image", event["image"], "https://s1.ticketm.net/large.jpg")
    check("tm price", (event["price"]["min"], event["price"]["max"]), (25.0, 45.0))
    check("tm currency", event["price"]["currency"], "CAD")
    check("tm artists", event["artists"], ["Headline Act"])
    check("tm genre from subGenre", event["genres"], ["techno"])
    check("tm ticket url", event["ticketUrl"], "https://www.ticketmaster.ca/event/G5vYZbLkQ")


def test_ticketmaster_skips_without_key() -> None:
    with Patch(ticketmaster.os, "environ", {}):
        check("tm unavailable without key", ticketmaster.available(), False)
        try:
            ticketmaster.fetch()
            FAILURES.append("tm should raise without an API key")
        except RuntimeError:
            pass


def test_ticketmaster_drops_undefined_genre() -> None:
    raw = dict(TM_EVENT)
    # "Undefined" is a literal value TM sends, and this title names no genre --
    # so the only correct outcome is the query-scoped fallback.
    raw["name"] = "Saturday Night At The Warehouse"
    raw["classifications"] = [{"genre": {"name": "Undefined"}, "subGenre": {"name": "Undefined"}}]
    page = {"_embedded": {"events": [raw]}, "page": {"totalPages": 1}}
    with Patch(ticketmaster, "request_json", _paged(page)), Patch(ticketmaster.time, "sleep", lambda s: None):
        with Patch(ticketmaster.os, "environ", {"TICKETMASTER_API_KEY": "k"}):
            events = ticketmaster.fetch(days_ahead=30)
    check("tm ignores Undefined genre", events[0]["genres"] if events else None, ["electronic"])


def test_ticketmaster_prefers_specific_genre_over_catchall() -> None:
    # TM always sends the "Dance/Electronic" umbrella alongside the real
    # subgenre; the umbrella must not survive as its own chip.
    page = {"_embedded": {"events": [TM_EVENT]}, "page": {"totalPages": 1}}
    with Patch(ticketmaster, "request_json", _paged(page)), Patch(ticketmaster.time, "sleep", lambda s: None):
        with Patch(ticketmaster.os, "environ", {"TICKETMASTER_API_KEY": "k"}):
            events = ticketmaster.fetch(days_ahead=30)
    check("catchall dropped when specific genre present", events[0]["genres"], ["techno"])


def test_showpass_disabled_by_default() -> None:
    with Patch(showpass.os, "environ", {}):
        check("showpass off unless opted in", showpass.available(), False)
    with Patch(showpass.os, "environ", {"ENABLE_SHOWPASS": "1"}):
        check("showpass on when enabled", showpass.available(), True)


def test_showpass() -> None:
    page = {"results": [SHOWPASS_EVENT]}
    empty = {"results": []}
    with Patch(showpass.os, "environ", {"ENABLE_SHOWPASS": "1"}), Patch(showpass, "request_json", _paged(page, empty)), Patch(showpass.time, "sleep", lambda s: None):
        events = showpass.fetch(days_ahead=30)

    check("showpass returns one event", len(events), 1)
    if not events:
        return
    event = events[0]
    check("showpass title", event["title"], "Deep House Sundays")
    check("showpass venue", event["venue"], "The Pearl")
    check("showpass address", event["address"], "1379 Granville St")
    check("showpass builds url from slug", event["url"], "https://www.showpass.com/deep-house-sundays/")
    check("showpass genre from tag", event["genres"], ["house"])
    check("showpass local time kept", event["start"][:16], "2026-08-16T21:00")


def test_showpass_bare_list_envelope() -> None:
    # Showpass has shipped both a bare list and a {"results": [...]} envelope.
    with Patch(showpass.os, "environ", {"ENABLE_SHOWPASS": "1"}), Patch(showpass, "request_json", _paged([SHOWPASS_EVENT], [])), Patch(showpass.time, "sleep", lambda s: None):
        events = showpass.fetch(days_ahead=30)
    check("showpass handles bare list", len(events), 1)


def test_showpass_filters_non_electronic() -> None:
    raw = dict(SHOWPASS_EVENT)
    raw["name"] = "Saturday Karaoke Party"
    raw["tags"] = []
    raw["description"] = "Sing your heart out."
    with Patch(showpass.os, "environ", {"ENABLE_SHOWPASS": "1"}), Patch(showpass, "request_json", _paged({"results": [raw]}, {"results": []})), \
            Patch(showpass.time, "sleep", lambda s: None):
        events = showpass.fetch(days_ahead=30)
    # Showpass carries every kind of event, so it gets no fallback genre.
    check("showpass drops unrelated events", len(events), 0)


def test_showpass_raises_when_all_endpoints_fail() -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("503")

    with Patch(showpass.os, "environ", {"ENABLE_SHOWPASS": "1"}), Patch(showpass, "request_json", boom), Patch(showpass.time, "sleep", lambda s: None):
        try:
            showpass.fetch(days_ahead=30)
            FAILURES.append("showpass should raise when every endpoint fails")
        except RuntimeError:
            pass


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FAILED ({len(FAILURES)})\n", file=sys.stderr)
        for failure in FAILURES:
            print(f"  ✗ {failure}\n", file=sys.stderr)
        return 1
    print("all source adapter tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
