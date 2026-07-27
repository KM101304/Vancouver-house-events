"""Shared event schema, genre classification, and dedupe logic.

Every source adapter returns a list of dicts in the shape produced by
``make_event``. Everything downstream (dedupe, ICS, the website) only ever
sees that shape, so adding a source never means touching the site.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    VANCOUVER = ZoneInfo("America/Vancouver")
except Exception:  # pragma: no cover - only on systems with no tz database
    # Pacific Daylight Time. Off by an hour for events in Nov-Mar, which is
    # better than crashing the whole refresh on a runner without tzdata.
    VANCOUVER = timezone(timedelta(hours=-7), "PDT")


SCHEMA_VERSION = 2

# Genre vocabulary. `PRIMARY` decides whether an event belongs on this site at
# all; `ADJACENT` rides along only when a primary genre is already present, so
# a disco-tagged funk band doesn't get pulled in on its own.
PRIMARY = {
    "house": ["house", "deep house", "tech house", "afro house", "acid house",
              "progressive house", "melodic house", "soulful house", "bass house",
              "amapiano", "gqom"],
    "techno": ["techno", "minimal", "melodic techno", "hard techno", "industrial techno",
               "acid techno", "dub techno"],
    "garage": ["garage", "uk garage", "ukg", "2-step", "bassline", "speed garage"],
    "breaks": ["breaks", "breakbeat", "electro", "ghettotech", "jungle"],
    "trance": ["trance", "psytrance", "progressive trance", "hard trance"],
    "dnb": ["drum and bass", "drum & bass", "drum n bass", "dnb", "d&b", "liquid"],
    # Catch-all for listings that are unambiguously electronic without naming a
    # subgenre -- used as the fallback for curated, scene-specific sources.
    "electronic": ["electronic", "edm", "dance music", "rave", "warehouse party"],
}
ADJACENT = {
    "disco": ["disco", "nu-disco", "nu disco", "italo", "boogie"],
    "ambient": ["ambient", "downtempo", "experimental", "leftfield", "idm"],
}

# Terms that mean "this is not a house night" even if a primary word appears
# somewhere in the blurb. RA lists everything programmed at the venues it
# covers, so a jazz trio at an electronic-adjacent room turns up in the feed.
EXCLUDE = [
    "karaoke", "trivia", "comedy", "open mic", "tribute band", "cover band",
    "country night", "metal", "hardcore punk", "singer-songwriter", "orchestra",
    "musical theatre", "wrestling", "burlesque bingo",
    # Acoustic-ensemble billing: "<Name> Quartet", "<Name> Trio".
    "quartet", "quintet", "sextet", "big band", "trio",
]

_ALL_GENRE_TERMS = [
    (label, term)
    for table in (PRIMARY, ADJACENT)
    for label, terms in table.items()
    for term in terms
]
# Longest first so "deep house" wins over "house" and sets the same label anyway,
# but more importantly so "drum and bass" isn't shadowed by a stray "bass".
_ALL_GENRE_TERMS.sort(key=lambda pair: len(pair[1]), reverse=True)

_PRIMARY_LABELS = set(PRIMARY)


def _slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def _clean(value) -> str:
    if value is None:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = text.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    text = text.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def classify(*texts: str) -> list[str]:
    """Return genre labels found in the given text, primary genres first."""
    haystack = " ".join(_clean(t).lower() for t in texts if t)
    if not haystack:
        return []

    found: list[str] = []
    for label, term in _ALL_GENRE_TERMS:
        if label in found:
            continue
        # Word-boundary match so "housewarming" doesn't count as house.
        if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", haystack):
            found.append(label)

    primary = [g for g in found if g in _PRIMARY_LABELS]
    if not primary:
        return []
    # "electronic" is a catch-all, not a peer genre. A listing tagged
    # "Dance/Electronic · Techno" is a techno night -- carrying both would put
    # an "electronic" chip on nearly every platform listing and say nothing.
    if len(primary) > 1:
        primary = [g for g in primary if g != "electronic"]
    adjacent = [g for g in found if g not in _PRIMARY_LABELS]
    return primary + adjacent


def is_excluded(*texts: str) -> bool:
    haystack = " ".join(_clean(t).lower() for t in texts if t)
    # Word-boundary matched: a substring test would kill "Jazzanova" on "jazz"
    # and "Metallic Sunset" on "metal".
    return any(
        re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", haystack)
        for term in EXCLUDE
    )


def parse_dt(value, fallback_time: str = "22:00") -> datetime | None:
    """Parse the many date shapes ticketing APIs emit into aware Vancouver time."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = None
        # A bare date means the API only gave us a day; assume a club start time.
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            text = f"{text}T{fallback_time}:00"
        for parser in (
            lambda t: datetime.fromisoformat(t),
            lambda t: datetime.strptime(t, "%Y-%m-%dT%H:%M:%S"),
            lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M:%S"),
            lambda t: datetime.strptime(t, "%Y/%m/%d %H:%M"),
        ):
            try:
                dt = parser(text)
                break
            except (ValueError, TypeError):
                continue
        if dt is None:
            return None

    if dt.tzinfo is None:
        # Naive timestamps from local ticketing platforms are local time.
        return dt.replace(tzinfo=VANCOUVER)
    return dt.astimezone(VANCOUVER)


def _is_afterhours(start: datetime, venue: str, title: str) -> bool:
    if 1 <= start.hour < 8:
        return True
    blob = f"{venue} {title}".lower()
    return "after hours" in blob or "afterhours" in blob or "gorg-o-mish" in blob


def make_event(
    *,
    source: str,
    title: str,
    start,
    venue: str,
    url: str = "",
    ticket_url: str = "",
    end=None,
    artists=None,
    genres=None,
    image: str = "",
    promoter: str = "",
    price_min=None,
    price_max=None,
    currency: str = "CAD",
    description: str = "",
    source_id: str = "",
    address: str = "",
    fallback_genres=None,
) -> dict | None:
    """Build one normalized event, or None if it isn't a usable house/techno listing."""
    title = _clean(title)
    venue = _clean(venue)
    start_dt = parse_dt(start)
    if not title or not start_dt:
        return None

    artists = [_clean(a) for a in (artists or []) if _clean(a)]
    # Some feeds repeat the venue or the event name in the lineup; drop that noise.
    artists = [a for a in dict.fromkeys(artists) if a.lower() not in {venue.lower(), title.lower()}]

    given = [_clean(g).lower() for g in (genres or []) if _clean(g)]

    # A genre the source itself asserts outranks a guess made from the title.
    # Without this, a confirmed house night called "Metal Disco" would be
    # thrown out by a keyword that only ever meant to catch metal gigs.
    confirmed = [g for g in classify(" ".join(given)) if g != "electronic"]
    if not confirmed and is_excluded(title, description):
        return None

    detected = classify(" ".join(given), title, description, " ".join(artists), promoter)
    if not detected:
        # Sources that only ever carry this scene (RA, curated listings) declare a
        # fallback so an unlabelled listing still lands instead of vanishing.
        detected = [g for g in (fallback_genres or []) if g]
    if not detected:
        return None

    end_dt = parse_dt(end)
    if end_dt and end_dt <= start_dt:
        # Feeds routinely give a 2am end time on the same calendar date.
        end_dt += timedelta(days=1)

    ident = source_id or f"{_slug(title)}|{start_dt:%Y-%m-%d}|{_slug(venue)}"
    event_id = hashlib.sha1(f"{source}:{ident}".encode()).hexdigest()[:16]

    return {
        "id": event_id,
        "title": title,
        "artists": artists,
        "venue": venue,
        "address": _clean(address),
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat() if end_dt else None,
        "date": start_dt.strftime("%Y-%m-%d"),
        # An event that starts at 2am belongs to the night before, which is how
        # anyone actually going to it thinks about the date.
        "night": (start_dt - timedelta(hours=6)).strftime("%Y-%m-%d"),
        "genres": detected,
        "url": url or ticket_url,
        "ticketUrl": ticket_url or url,
        "image": image,
        "promoter": _clean(promoter),
        "price": {"min": price_min, "max": price_max, "currency": currency}
        if price_min is not None
        else None,
        "afterhours": _is_afterhours(start_dt, venue, title),
        "source": source,
    }


def _dedupe_key(event: dict) -> str:
    """Same night + same venue + similar title == the same party."""
    title = re.sub(r"\b(presents|pres\.?|feat\.?|featuring|w/|with)\b", " ", event["title"].lower())
    # Drop the lineup half of "Promoter presents: Artist" style titles so the
    # RA and Ticketmaster spellings of one night collapse together.
    title = _slug(title)[:28]
    return f"{event['night']}|{_slug(event['venue'])[:20]}|{title}"


def _richness(event: dict) -> tuple:
    return (
        1 if event.get("ticketUrl") else 0,
        len(event.get("artists") or []),
        len(event.get("genres") or []),
        1 if event.get("image") else 0,
        1 if event.get("price") else 0,
        len(event.get("title") or ""),
    )


def dedupe(events: list[dict], source_priority: list[str]) -> list[dict]:
    """Collapse the same party seen on multiple platforms into one listing."""
    rank = {name: i for i, name in enumerate(source_priority)}
    buckets: dict[str, list[dict]] = {}
    for event in events:
        buckets.setdefault(_dedupe_key(event), []).append(event)

    merged = []
    for group in buckets.values():
        group.sort(key=lambda e: (rank.get(e["source"], 99), [-x for x in _richness(e)]))
        winner = dict(group[0])
        others = group[1:]
        if others:
            winner["alsoOn"] = sorted({e["source"] for e in others})
            # Fill gaps in the winner from the runners-up rather than losing data.
            for other in others:
                for field in ("image", "ticketUrl", "url", "promoter", "address", "end"):
                    if not winner.get(field) and other.get(field):
                        winner[field] = other[field]
                if not winner.get("price") and other.get("price"):
                    winner["price"] = other["price"]
                for key in ("artists", "genres"):
                    have = {v.lower() for v in winner.get(key) or []}
                    winner[key] = (winner.get(key) or []) + [
                        v for v in other.get(key) or [] if v.lower() not in have
                    ]
        merged.append(winner)

    merged.sort(key=lambda e: (e["start"], e["venue"]))
    return merged
