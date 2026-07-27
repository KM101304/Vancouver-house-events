"""Shared event schema, genre classification, and dedupe logic.

Every source adapter returns a list of dicts in the shape produced by
``make_event``. Everything downstream (dedupe, ICS, the website) only ever
sees that shape, so adding a source never means touching the site.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    "house": ["house", "deep house", "tech house", "acid house", "progressive house",
              "melodic house", "soulful house", "bass house", "disco house",
              "funky house", "jackin house", "jackin' house", "garage house",
              "microhouse", "micro house", "lo-fi house", "organic house",
              "indie dance", "french house"],
    "techno": ["techno", "minimal", "melodic techno", "hard techno", "industrial techno",
               "acid techno", "dub techno", "hypnotic techno", "raw techno",
               "peak time", "electro techno"],
    # Afro-electronic, which increasingly shares the same rooms and line-ups.
    "afro": ["afrobeat", "afrobeats", "afro house", "afro tech", "afro deep",
             "amapiano", "gqom", "kuduro", "azonto", "afro disco"],
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

# What actually belongs on this site. The classifier still labels everything it
# recognises -- dnb, trance, breaks -- but only these qualify a listing for
# inclusion. Widen this set to broaden the site; it is the single knob.
IN_SCOPE = {"house", "techno", "afro", "disco", "garage"}

# Words that mark a listing as a club night even when no subgenre is named.
# Most underground parties are billed by promoter and line-up, not by genre, so
# without these the strict gate throws out real events with cryptic titles.
CLUB_SIGNALS = [
    "rave", "dancefloor", "dance floor", "after party", "afterparty", "afters",
    "warehouse", "all night", "all-night", "b2b", "back to back", "dj set",
    "djs", "dj ", "selector", "sound system", "soundsystem", "basement",
    "underground", "club night", "day party", "block party", "boiler room",
    "residents", "resident dj", "late night", "til late", "till late",
]


def has_club_signal(*texts: str) -> bool:
    haystack = " ".join(_clean(t).lower() for t in texts if t)
    return any(
        re.search(r"(?<![a-z])" + re.escape(term.strip()) + r"(?![a-z])", haystack)
        for term in CLUB_SIGNALS
    )

# Terms that mean "this is not a house night" even if a primary word appears
# somewhere in the blurb. RA lists everything programmed at the venues it
# covers, so a jazz trio at an electronic-adjacent room turns up in the feed.
EXCLUDE = [
    "karaoke", "trivia", "comedy", "open mic", "tribute band", "cover band",
    "country night", "metal", "hardcore punk", "singer-songwriter", "orchestra",
    "musical theatre", "wrestling", "burlesque bingo",
    # Acoustic-ensemble billing: "<Name> Quartet", "<Name> Trio".
    "quartet", "quintet", "sextet", "big band", "trio",
    # RA's Vancouver area carries the film festivals programmed at the same
    # rooms; they were the second-largest "promoter" in the live feed.
    "film festival", "screening", "screenings", "documentary", "short film",
    "cinema", "film fest", "in conversation", "panel discussion",
    # Seated listening events are not club nights.
    "deep listen", "listening session", "listening party", "album playback",
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


def _load_scene() -> tuple[list[str], list[str], list[str]]:
    """Curated promoters and rooms that reliably programme this music."""
    path = Path(__file__).resolve().parents[1] / "data" / "scene.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return (
            [p.lower() for p in payload.get("promoters", []) if p],
            [v.lower() for v in payload.get("venues", []) if v],
            [p.lower() for p in payload.get("notPromoters", []) if p],
        )
    except (OSError, json.JSONDecodeError):
        return [], [], []


SCENE_PROMOTERS, SCENE_VENUES, NOT_PROMOTERS = _load_scene()


def is_off_scene(promoter: str) -> bool:
    """Promoters known to book other music -- film festivals, rock bookers."""
    promoter = (promoter or "").lower()
    return bool(promoter) and any(name in promoter for name in NOT_PROMOTERS)


def is_scene(promoter: str, venue: str) -> bool:
    """True when a known house/techno promoter or room is behind the listing."""
    promoter = (promoter or "").lower()
    venue = (venue or "").lower()
    return (
        any(name in promoter for name in SCENE_PROMOTERS)
        or any(name in venue for name in SCENE_VENUES)
    )


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
    is_scene_source: bool = False,
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

    detected = classify(" ".join(given), title, description, " ".join(artists), promoter)
    in_scope = [g for g in detected if g in IN_SCOPE]

    # A genre the source itself asserts outranks a guess made from the title.
    # Without this, a confirmed house night called "Metal Disco" would be
    # thrown out by a keyword that only ever meant to catch metal gigs.
    asserted = [g for g in classify(" ".join(given)) if g in IN_SCOPE]
    if not asserted and is_excluded(title, description, promoter):
        return None

    # A promoter who books other music loses the benefit of the doubt -- but a
    # listing that names an in-scope genre outright still stands on its own.
    if not in_scope and is_off_scene(promoter):
        return None

    # Inclusion needs positive evidence of the right music. A generic
    # "electronic" tag is not evidence -- it is what let a film festival and a
    # jazz trio onto a house and techno site.
    specific = [g for g in detected if g != "electronic"]
    if in_scope:
        pass
    elif specific:
        # We positively identified what this is and it isn't house, techno or
        # afro -- a drum & bass or trance night. Knowing beats blanket trust.
        return None
    elif has_club_signal(title, description, promoter) or is_scene(promoter, venue):
        # It reads as a club night, or a known room/promoter vouches for it.
        # Label it "electronic" rather than inventing a subgenre we don't know.
        detected = detected or list(fallback_genres or []) or ["electronic"]
    elif fallback_genres and is_scene_source:
        # A source that only carries this music (RA) is itself evidence, and
        # the exclusion list above has already removed the film and rock nights
        # it also lists. Dropping the rest would lose most of the real feed.
        detected = list(fallback_genres)
    else:
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
