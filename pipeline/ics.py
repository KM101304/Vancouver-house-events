"""Generate an iCalendar feed so people can subscribe to the listings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .normalize import parse_dt


def _escape(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """RFC 5545 caps content lines at 75 octets."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 73:
        return line
    chunks, current = [], b""
    for char in line:
        raw = char.encode("utf-8")
        if len(current) + len(raw) > 73:
            chunks.append(current.decode("utf-8"))
            current = b" " + raw
        else:
            current += raw
    chunks.append(current.decode("utf-8"))
    return "\r\n".join(chunks)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build(events: list[dict], *, name: str = "Vancouver House Events") -> str:
    now = datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//vancouver-house-events//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
        "X-WR-TIMEZONE:America/Vancouver",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
    ]

    for event in events:
        start = parse_dt(event.get("start"))
        if not start:
            continue
        end = parse_dt(event.get("end")) or start + timedelta(hours=5)

        summary = event["title"]
        if event.get("artists"):
            summary = f"{summary} — {', '.join(event['artists'][:4])}"

        description = []
        if event.get("artists"):
            description.append("Lineup: " + ", ".join(event["artists"]))
        if event.get("promoter"):
            description.append("Presented by " + event["promoter"])
        if event.get("genres"):
            description.append("Genres: " + ", ".join(event["genres"]))
        if event.get("ticketUrl"):
            description.append("Tickets: " + event["ticketUrl"])
        description.append("via " + event.get("source", "vancouver-house-events"))

        lines += [
            "BEGIN:VEVENT",
            f"UID:{event['id']}@vancouver-house-events",
            f"DTSTAMP:{_stamp(now)}",
            f"DTSTART:{_stamp(start)}",
            f"DTEND:{_stamp(end)}",
            _fold(f"SUMMARY:{_escape(summary)}"),
            _fold(f"LOCATION:{_escape(event.get('venue') or '')}"),
            _fold(f"DESCRIPTION:{_escape(chr(10).join(description))}"),
        ]
        if event.get("url"):
            lines.append(_fold(f"URL:{event['url']}"))
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
