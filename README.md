# After Dark — Vancouver house & techno listings

Vancouver's house scene is scattered across Resident Advisor, a dozen ticketing
platforms and a lot of Instagram stories. This pulls it into one page, one open
data feed, and one calendar you can subscribe to.

**Site:** https://km101304.github.io/Vancouver-house-events/
*(live once GitHub Pages is enabled — see setup below)*

---

## What it does

A scheduled job hits every source every six hours, normalizes wildly different
payloads into one schema, throws out anything that isn't house/techno adjacent,
merges the same party appearing on three platforms into one listing, and commits
the result as static JSON. The site is plain HTML/CSS/JS reading that file — no
build step, no framework, no server.

### Feeds

Static, versioned, CORS-open. No key, no rate limit, no account.

| Feed | What's in it |
|---|---|
| [`docs/data/events.json`](docs/data/events.json) | Everything upcoming, one schema |
| [`docs/data/events.ics`](docs/data/events.ics) | Calendar subscription |
| [`docs/data/venues.json`](docs/data/venues.json) | Rooms currently programming |
| [`docs/data/meta.json`](docs/data/meta.json) | Last refresh + per-source health |

```json
{
  "id": "a1b2c3d4e5f60718",
  "title": "Basement Sessions 004",
  "artists": ["Local Resident"],
  "venue": "Open Studios",
  "start": "2026-08-15T23:00:00-07:00",
  "end": "2026-08-16T04:00:00-07:00",
  "date": "2026-08-15",
  "night": "2026-08-15",
  "genres": ["house", "disco"],
  "ticketUrl": "https://…",
  "price": { "min": 20, "max": null, "currency": "CAD" },
  "afterhours": false,
  "source": "residentadvisor"
}
```

`night` is the one worth knowing about: a set starting at 2am is filed under the
evening before, because that's the night you went out.

## Sources

| Source | Key needed | Notes |
|---|---|---|
| **Curated** (`data/manual-events.json`) | — | Hand-added nights. Wins every dedupe tie. |
| **Resident Advisor** | — | Public GraphQL endpoint. Best lineup and promoter data. |
| **Ticketmaster** | `TICKETMASTER_API_KEY` | Skips itself cleanly if unset. Bigger rooms, touring acts. |
| **Showpass** | `ENABLE_SHOWPASS=1` | Off by default — see below. |

**Resident Advisor genres.** RA's listing payload carries no per-event genre,
so the pipeline re-runs the query once per genre facet RA offers and records
what comes back. This is purely additive: if RA rejects the genre filter the
plain listings are kept, and enrichment is only trusted when it covers at least
half the listings — a partial map would otherwise look like "these aren't dance
events". When genres *are* available, a listing matching no facet is dropped,
which is what keeps the jazz and rock that RA also lists at these rooms off the
site.

**Showpass is disabled by default.** It has no published listings endpoint and
the paths tried returned nothing usable in production, so leaving it on only
decorates the footer with a permanent failure. The adapter is kept because the
platform genuinely matters here — set `ENABLE_SHOWPASS=1` once the real path is
known, and fix `ENDPOINTS` in `pipeline/sources/showpass.py`.

A source that breaks **cannot empty the site.** Each adapter runs isolated; if
one fails, its still-upcoming events from the previous run are carried forward,
flagged `stale`, shown as `unconfirmed` on the site, and the failure is recorded
in `meta.json` and printed in the footer. Stale entries expire after 21 days so
a permanently broken feed doesn't serve ghosts forever.

## Setup

**1. Turn on GitHub Pages.** Settings → Pages → Source: *Deploy from a branch* →
branch `main`, folder `/docs`. Nothing to build; every data commit redeploys.

**2. Let Actions write to the repo.** Settings → Actions → General → Workflow
permissions → *Read and write*. Without this the refresh job can't commit.

**3. Optional — add Ticketmaster.** Grab a free key at
[developer.ticketmaster.com](https://developer.ticketmaster.com/), then
Settings → Secrets and variables → Actions → new secret `TICKETMASTER_API_KEY`.
Skip it and the other sources carry on.

**4. Kick off the first pull.** Actions → *Refresh event feeds* → Run workflow.
Until this runs, the site correctly shows an empty state — no listings are
invented.

## Adding a night by hand

Most of the good ones never touch a ticketing API. Edit
[`data/manual-events.json`](data/manual-events.json):

```json
{
  "title": "Basement Sessions 004",
  "start": "2026-08-15T23:00:00",
  "venue": "Open Studios",
  "artists": ["Local Resident"],
  "genres": ["house"],
  "ticketUrl": "https://…"
}
```

Only `title`, `start` and `venue` are required. Set `"draft": true` to park one
without publishing. Or just open an issue with the
[submission form](../../issues/new?template=submit-event.yml).

## Local development

```bash
python -m pipeline.test_pipeline        # logic tests, no network
python -m pipeline.ingest --days 60     # refresh feeds
python -m pipeline.ingest --only manual # one source
python -m http.server 8000 -d docs      # serve the site
```

## Layout

```
pipeline/
  ingest.py          orchestrator — runs sources, merges, writes feeds
  normalize.py       schema, genre classification, dedupe
  ics.py             calendar generation
  sources/           one module per platform, each returns normalized events
  test_pipeline.py   offline tests
data/
  manual-events.json curated listings
docs/                the website + generated feeds (GitHub Pages root)
```

Adding a source means writing one module with a `fetch()` that returns
`make_event(...)` results, then adding it to `SOURCES` in `ingest.py`. Nothing
downstream needs to change.

## Design

The page is an instrument, not a landing page. Its primary object is a 13-hour
axis (19:00 → 08:00) with every party drawn at its real hours, because this
scene has a shape: clubs call last round at 3, the afterhours rooms run to 8.
Reading that handover is the point — overlapping parties are lane-packed so a
busy Saturday stacks instead of colliding.

Genre colour is therefore *functional*, not decoration: it is what makes the
plot legible. The seven hues are a categorical palette validated for
colour-vision-deficiency separation and contrast against the plot surface, and
identity is never carried by colour alone — every bar and row states its genre
in text, and the legend doubles as the genre filter.

Deliberately absent: hero banner, gradient fills, rounded cards with an accent
stripe, a lone neon accent, emoji.

## Notes

This is an index, not a box office — tickets are sold by the venues and
promoters, and listings and artwork belong to them. Genre filtering is keyword
based and deliberately errs toward including a night rather than dropping it.
