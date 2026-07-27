"""Offline tests for the normalization, dedupe and calendar logic.

Run: python -m pipeline.test_pipeline
No network required -- the source adapters are exercised in CI against the
real APIs, but everything decision-making lives here and is tested directly.
"""

from __future__ import annotations

import sys

from . import ics
from .normalize import classify, dedupe, make_event, parse_dt

FAILURES: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual != expected:
        FAILURES.append(f"{label}\n    expected: {expected!r}\n    actual:   {actual!r}")


def check_true(label: str, value) -> None:
    if not value:
        FAILURES.append(f"{label}\n    expected truthy, got {value!r}")


def test_classify() -> None:
    check("plain house", classify("Deep House night"), ["house"])
    check("techno + disco", classify("techno & nu-disco"), ["techno", "disco"])
    # "housewarming" must not register as house.
    check("word boundary", classify("housewarming party"), [])
    # Adjacent genres never qualify an event on their own.
    check("adjacent alone", classify("italo disco records"), [])
    check("dnb long form", classify("drum and bass night"), ["dnb"])
    check("empty", classify(""), [])
    # The catch-all only survives when nothing specific matched.
    check("catchall alone", classify("electronic music night"), ["electronic"])
    check("catchall demoted", classify("Dance/Electronic · Techno"), ["techno"])
    check("catchall demoted w/ adjacent", classify("electronic house and disco"), ["house", "disco"])


def test_make_event() -> None:
    event = make_event(
        source="test",
        title="Warehouse Techno",
        start="2026-08-15T23:30:00",
        venue="Some Room",
        artists=["DJ One", "DJ One", "Some Room"],
        ticket_url="https://tickets.example/1",
    )
    check_true("event built", event)
    check("genre detected", event["genres"], ["techno"])
    # Duplicates and venue-name noise get stripped from the lineup.
    check("artists cleaned", event["artists"], ["DJ One"])
    check("night is start date", event["night"], "2026-08-15")
    check("url falls back to ticket url", event["url"], "https://tickets.example/1")

    # A 2am start belongs to the previous night.
    afterhours = make_event(
        source="test", title="Afterhours Techno", start="2026-08-16T02:00:00", venue="Basement"
    )
    check("2am files under prior night", afterhours["night"], "2026-08-15")
    check_true("2am flagged afterhours", afterhours["afterhours"])

    # An end time earlier than the start means it rolled past midnight.
    rollover = make_event(
        source="test", title="House Night", start="2026-08-15T22:00:00",
        end="2026-08-15T03:00:00", venue="Room",
    )
    check("end rolls to next day", rollover["end"][:10], "2026-08-16")

    check("non-house rejected", make_event(source="t", title="Country Night", start="2026-08-15T20:00:00", venue="Bar"), None)
    check("excluded word wins", make_event(source="t", title="House Karaoke", start="2026-08-15T20:00:00", venue="Bar"), None)
    check("no title rejected", make_event(source="t", title="", start="2026-08-15T20:00:00", venue="Bar"), None)
    check("no date rejected", make_event(source="t", title="Techno", start=None, venue="Bar"), None)

    # A trusted source can carry an unlabelled listing through.
    fallback = make_event(
        source="ra", title="Mysterious Night 004", start="2026-08-15T23:00:00",
        venue="Room", fallback_genres=["electronic"],
    )
    check("fallback genre applied", fallback["genres"], ["electronic"])


def test_dedupe() -> None:
    shared = {"start": "2026-08-15T23:00:00", "venue": "Celebrities"}
    rich = make_event(
        source="residentadvisor", title="Big Techno Night", artists=["A", "B"],
        ticket_url="https://ra.co/events/1", image="https://img/1.jpg", **shared,
    )
    thin = make_event(source="showpass", title="Big Techno Night", **shared)
    thin["promoter"] = "Some Promoter"

    merged = dedupe([thin, rich], ["manual", "residentadvisor", "ticketmaster", "showpass"])
    check("duplicates collapsed", len(merged), 1)
    check("richer source wins", merged[0]["source"], "residentadvisor")
    check("cross-source noted", merged[0]["alsoOn"], ["showpass"])
    # Fields missing on the winner are back-filled from the loser.
    check("gap filled from loser", merged[0]["promoter"], "Some Promoter")

    # Same name, different night -> two separate listings.
    other_night = make_event(source="showpass", title="Big Techno Night", start="2026-08-22T23:00:00", venue="Celebrities")
    check("different nights kept apart", len(dedupe([rich, other_night], ["residentadvisor", "showpass"])), 2)


def test_parse_dt() -> None:
    check("bare date gets club start", parse_dt("2026-08-15").hour, 22)
    check("utc converts to local", parse_dt("2026-08-16T06:00:00Z").hour, 23)
    check("empty is none", parse_dt(""), None)
    check("garbage is none", parse_dt("not a date"), None)


def test_ics() -> None:
    event = make_event(
        source="test", title="Techno Night", start="2026-08-15T23:00:00",
        venue="Room, With Comma", artists=["DJ One"],
    )
    text = ics.build([event])
    check_true("has calendar wrapper", text.startswith("BEGIN:VCALENDAR"))
    check_true("ends properly", text.strip().endswith("END:VCALENDAR"))
    check_true("crlf line endings", "\r\n" in text)
    # Commas in a LOCATION must be escaped or the file won't parse.
    check_true("comma escaped", "Room\\, With Comma" in text)
    check_true("uid present", f"UID:{event['id']}" in text)
    # No end time given -> a default duration is still emitted.
    check_true("dtend emitted", "DTEND:" in text)

    long_title = make_event(
        source="test", title="Techno " + "Very Long Title " * 12,
        start="2026-08-15T23:00:00", venue="Room",
    )
    for line in ics.build([long_title]).split("\r\n"):
        if len(line.encode("utf-8")) > 75:
            FAILURES.append(f"ics line exceeds 75 octets: {line[:60]}…")
            break


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FAILED ({len(FAILURES)})\n", file=sys.stderr)
        for failure in FAILURES:
            print(f"  ✗ {failure}\n", file=sys.stderr)
        return 1
    print("all pipeline tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
