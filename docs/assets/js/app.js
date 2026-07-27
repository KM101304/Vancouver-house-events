/* After Dark — listings client.
 *
 * Event data comes from third-party APIs, so nothing from the feed is ever
 * interpolated into innerHTML. Text goes through textContent and URLs are
 * scheme-checked before they reach an href.
 */
(function () {
  "use strict";

  var DATA_URL = "data/events.json";
  var VANCOUVER = "America/Vancouver";

  var state = {
    events: [],
    meta: null,
    when: "all",
    genres: new Set(),
    venue: "",
    afterhours: false,
    query: ""
  };

  var $ = function (sel) { return document.querySelector(sel); };
  var role = function (name) { return document.querySelector('[data-role="' + name + '"]'); };

  /* ---------------------------------------------------------------- utils */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  // Only http(s) links get rendered; blocks javascript: and data: URLs that
  // could ride in on a compromised or sloppy upstream feed.
  function safeUrl(value) {
    if (!value) return null;
    try {
      var parsed = new URL(value, window.location.href);
      return (parsed.protocol === "http:" || parsed.protocol === "https:") ? parsed.href : null;
    } catch (err) {
      return null;
    }
  }

  function parseDate(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }

  // "The night of" — anything before 6am belongs to the previous evening.
  function nightKey(date) {
    var shifted = new Date(date.getTime() - 6 * 3600 * 1000);
    return ymd(shifted);
  }

  function ymd(date) {
    var parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: VANCOUVER, year: "numeric", month: "2-digit", day: "2-digit"
    }).formatToParts(date).reduce(function (acc, p) { acc[p.type] = p.value; return acc; }, {});
    return parts.year + "-" + parts.month + "-" + parts.day;
  }

  function fmt(date, options) {
    options.timeZone = VANCOUVER;
    return new Intl.DateTimeFormat("en-CA", options).format(date);
  }

  function nowInVancouver() {
    return new Date();
  }

  function currentNight() {
    return nightKey(nowInVancouver());
  }

  /* ------------------------------------------------------------ filtering */

  function matchesQuery(event, query) {
    if (!query) return true;
    var haystack = [
      event.title, event.venue, event.promoter,
      (event.artists || []).join(" "), (event.genres || []).join(" ")
    ].join(" ").toLowerCase();
    // Every whitespace-separated term must appear somewhere.
    return query.split(/\s+/).every(function (term) { return haystack.indexOf(term) !== -1; });
  }

  function withinWhen(event, when) {
    if (when === "all") return true;

    var tonight = currentNight();
    if (when === "tonight") return event.night === tonight;

    var start = parseDate(event.start);
    if (!start) return false;
    var days = Math.floor((new Date(event.night + "T12:00:00") - new Date(tonight + "T12:00:00")) / 86400000);
    if (days < 0) return false;

    if (when === "week") return days <= 6;
    if (when === "weekend") {
      // Friday or Saturday night, within the coming week.
      var dow = fmt(new Date(event.night + "T12:00:00"), { weekday: "short" });
      return days <= 7 && (dow === "Fri" || dow === "Sat");
    }
    return true;
  }

  function visibleEvents() {
    var query = state.query.trim().toLowerCase();
    return state.events.filter(function (event) {
      if (!withinWhen(event, state.when)) return false;
      if (state.venue && event.venue !== state.venue) return false;
      if (state.afterhours && !event.afterhours) return false;
      if (state.genres.size) {
        var hit = (event.genres || []).some(function (g) { return state.genres.has(g); });
        if (!hit) return false;
      }
      return matchesQuery(event, query);
    });
  }

  /* ------------------------------------------------------------- calendar */

  function icsFor(event) {
    var start = parseDate(event.start);
    var end = parseDate(event.end) || new Date(start.getTime() + 5 * 3600 * 1000);
    var stamp = function (d) { return d.toISOString().replace(/[-:]/g, "").split(".")[0] + "Z"; };
    var esc = function (s) { return String(s || "").replace(/([,;\\])/g, "\\$1").replace(/\n/g, "\\n"); };

    var summary = event.title;
    if (event.artists && event.artists.length) summary += " — " + event.artists.slice(0, 4).join(", ");

    return [
      "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//after-dark//EN",
      "BEGIN:VEVENT",
      "UID:" + event.id + "@vancouver-house-events",
      "DTSTAMP:" + stamp(new Date()),
      "DTSTART:" + stamp(start),
      "DTEND:" + stamp(end),
      "SUMMARY:" + esc(summary),
      "LOCATION:" + esc(event.venue),
      "DESCRIPTION:" + esc(event.ticketUrl ? "Tickets: " + event.ticketUrl : ""),
      "END:VEVENT", "END:VCALENDAR"
    ].join("\r\n");
  }

  function downloadIcs(event) {
    var blob = new Blob([icsFor(event)], { type: "text/calendar;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = el("a");
    link.href = url;
    link.download = (event.title || "event").replace(/[^a-z0-9]+/gi, "-").toLowerCase() + ".ics";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  /* -------------------------------------------------------------- render */

  function renderGig(event) {
    var row = el("article", "gig");

    var start = parseDate(event.start);
    var time = el("div", "gig__time");
    time.appendChild(el("span", null, start ? fmt(start, { hour: "2-digit", minute: "2-digit", hour12: false }) : "--:--"));

    // A 02:30 set listed under Thursday actually happens on Friday morning.
    // Without this the clock time reads as an hour that already passed.
    var notes = [];
    if (start && ymd(start) !== event.night) {
      notes.push(fmt(start, { weekday: "short" }) + " am");
    }
    if (event.end) {
      var end = parseDate(event.end);
      if (end) notes.push("til " + fmt(end, { hour: "2-digit", minute: "2-digit", hour12: false }));
    }
    if (notes.length) time.appendChild(el("small", null, notes.join(" · ")));
    row.appendChild(time);

    var main = el("div", "gig__main");

    var title = el("h3", "gig__title");
    var href = safeUrl(event.url) || safeUrl(event.ticketUrl);
    if (href) {
      var link = el("a", null, event.title);
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      title.appendChild(link);
    } else {
      title.textContent = event.title;
    }
    main.appendChild(title);

    if (event.artists && event.artists.length) {
      main.appendChild(el("p", "gig__lineup", event.artists.join(" · ")));
    } else if (event.promoter) {
      main.appendChild(el("p", "gig__lineup", event.promoter));
    }

    var facts = el("p", "gig__facts");
    if (event.venue) facts.appendChild(el("span", "gig__venue", event.venue));
    (event.genres || []).slice(0, 3).forEach(function (genre) {
      facts.appendChild(el("span", "tag", genre));
    });
    if (event.afterhours) facts.appendChild(el("span", "tag tag--afterhours", "afterhours"));
    if (event.stale) {
      var stale = el("span", "tag tag--stale", "unconfirmed");
      stale.title = "This source was unreachable on the last refresh — check with the venue.";
      facts.appendChild(stale);
    }
    main.appendChild(facts);
    row.appendChild(main);

    var aside = el("div", "gig__aside");
    if (event.price && event.price.min != null) {
      var price = "$" + Math.round(event.price.min);
      if (event.price.max != null && event.price.max > event.price.min) price += "–" + Math.round(event.price.max);
      aside.appendChild(el("span", "gig__price", price));
    }

    var ticketHref = safeUrl(event.ticketUrl);
    if (ticketHref) {
      var buy = el("a", "btn btn--solid btn--mini", "Tickets");
      buy.href = ticketHref;
      buy.target = "_blank";
      buy.rel = "noopener noreferrer";
      aside.appendChild(buy);
    }

    var add = el("button", "btn btn--ghost btn--mini", "+ Calendar");
    add.type = "button";
    add.setAttribute("aria-label", "Add " + event.title + " to calendar");
    add.addEventListener("click", function () { downloadIcs(event); });
    aside.appendChild(add);

    row.appendChild(aside);
    return row;
  }

  function renderListings() {
    var container = role("listings");
    var events = visibleEvents();
    container.textContent = "";

    if (!events.length) {
      container.appendChild(emptyState());
      $(".listings").setAttribute("aria-busy", "false");
      updateResetVisibility();
      return;
    }

    var tonight = currentNight();
    var groups = [];
    var index = {};
    events.forEach(function (event) {
      if (!index[event.night]) {
        index[event.night] = { night: event.night, events: [] };
        groups.push(index[event.night]);
      }
      index[event.night].events.push(event);
    });

    groups.forEach(function (group) {
      var date = new Date(group.night + "T12:00:00");
      var isTonight = group.night === tonight;

      var head = el("div", "nightbreak" + (isTonight ? " nightbreak--tonight" : ""));
      head.appendChild(el("h2", "nightbreak__day", isTonight ? "Tonight" : fmt(date, { weekday: "long" })));
      head.appendChild(el("span", "nightbreak__date", fmt(date, { month: "short", day: "numeric" })));
      head.appendChild(el("span", "nightbreak__count", group.events.length + (group.events.length === 1 ? " party" : " parties")));
      container.appendChild(head);

      group.events.forEach(function (event) { container.appendChild(renderGig(event)); });
    });

    $(".listings").setAttribute("aria-busy", "false");
    updateResetVisibility();
  }

  function emptyState() {
    var wrap = el("div", "state");
    var filtered = state.events.length > 0;

    if (filtered) {
      wrap.appendChild(el("h2", "state__title", "Nothing matches"));
      wrap.appendChild(el("p", "state__body", "No nights fit those filters. Try widening the date range or clearing a genre."));
      var clear = el("button", "btn btn--ghost", "Clear filters");
      clear.type = "button";
      clear.addEventListener("click", resetFilters);
      wrap.appendChild(clear);
      return wrap;
    }

    // No events at all — either the feeds haven't run yet or they're all down.
    wrap.appendChild(el("h2", "state__title", "Nothing listed yet"));
    var failed = state.meta && (state.meta.sources || []).filter(function (s) { return s.status === "failed"; });
    var message = (failed && failed.length)
      ? "The feeds came back empty on the last refresh. They retry automatically every six hours."
      : "The first feed refresh hasn't run yet. Once it does, the city shows up here.";
    wrap.appendChild(el("p", "state__body", message));

    var submit = el("a", "btn btn--solid", "Add a night");
    submit.href = submitUrl();
    submit.target = "_blank";
    submit.rel = "noopener noreferrer";
    wrap.appendChild(submit);
    return wrap;
  }

  function renderFilters() {
    var counts = {};
    state.events.forEach(function (event) {
      (event.genres || []).forEach(function (g) { counts[g] = (counts[g] || 0) + 1; });
    });

    var box = role("genre-filters");
    box.textContent = "";
    Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; }).forEach(function (genre) {
      var chip = el("button", "chip" + (state.genres.has(genre) ? " is-active" : ""));
      chip.type = "button";
      chip.setAttribute("aria-pressed", state.genres.has(genre) ? "true" : "false");
      chip.appendChild(document.createTextNode(genre));
      chip.appendChild(el("span", "chip__count", counts[genre]));
      chip.addEventListener("click", function () {
        if (state.genres.has(genre)) state.genres.delete(genre); else state.genres.add(genre);
        renderFilters();
        renderListings();
      });
      box.appendChild(chip);
    });

    var venues = {};
    state.events.forEach(function (event) {
      if (event.venue) venues[event.venue] = (venues[event.venue] || 0) + 1;
    });
    var select = role("venue-filter");
    select.textContent = "";
    select.appendChild(new Option("All rooms", ""));
    Object.keys(venues).sort(function (a, b) { return venues[b] - venues[a] || a.localeCompare(b); })
      .forEach(function (name) {
        var option = new Option(name + " (" + venues[name] + ")", name);
        if (name === state.venue) option.selected = true;
        select.appendChild(option);
      });
  }

  function renderVenues() {
    var list = role("venue-list");
    list.textContent = "";
    var counts = {};
    state.events.forEach(function (event) {
      if (!event.venue) return;
      if (!counts[event.venue]) counts[event.venue] = { name: event.venue, count: 0 };
      counts[event.venue].count += 1;
    });

    var venues = Object.keys(counts).map(function (k) { return counts[k]; })
      .sort(function (a, b) { return b.count - a.count || a.name.localeCompare(b.name); })
      .slice(0, 12);

    if (!venues.length) {
      list.appendChild(el("li", "footer__meta", "No rooms listed yet."));
      return;
    }

    venues.forEach(function (venue) {
      var item = el("li", "venue");
      var button = el("button", "venue__name", venue.name);
      button.type = "button";
      button.addEventListener("click", function () {
        state.venue = state.venue === venue.name ? "" : venue.name;
        state.when = "all";
        syncWhenChips();
        renderFilters();
        renderListings();
        document.getElementById("listings").scrollIntoView({ behavior: "smooth", block: "start" });
      });
      item.appendChild(button);
      item.appendChild(el("span", "venue__count", venue.count));
      list.appendChild(item);
    });
  }

  function renderTallies() {
    var tonight = currentNight();
    role("tally-events").textContent = state.events.length;
    var venues = {};
    state.events.forEach(function (e) { if (e.venue) venues[e.venue] = 1; });
    role("tally-venues").textContent = Object.keys(venues).length;

    var onTonight = state.events.filter(function (e) { return e.night === tonight; }).length;
    role("tally-tonight").textContent = onTonight;
    // A quiet Tuesday is real information — don't stamp it in fluorescent pink.
    role("stamp").classList.toggle("is-empty", onTonight === 0);
    role("stamp").querySelector(".stamp__label").textContent =
      onTonight === 1 ? "on tonight" : onTonight === 0 ? "nothing tonight" : "on tonight";
  }

  function renderSources() {
    var list = role("source-list");
    list.textContent = "";
    var sources = (state.meta && state.meta.sources) || [];
    if (!sources.length) { role("source-health").hidden = true; return; }

    sources.forEach(function (source) {
      var item = el("li", "source source--" + source.status);
      item.appendChild(el("span", "source__dot"));
      item.appendChild(el("span", null, source.label || source.source));
      var note = source.status === "ok" ? source.count + " listed"
        : source.status === "skipped" ? "not configured"
        : "unreachable";
      item.appendChild(el("span", "source__count", note));
      if (source.detail) item.title = source.detail;
      list.appendChild(item);
    });

    if (state.meta && state.meta.updated) {
      var updated = parseDate(state.meta.updated);
      if (updated) {
        role("updated").textContent = fmt(updated, {
          weekday: "short", month: "short", day: "numeric",
          hour: "2-digit", minute: "2-digit", hour12: false
        });
      }
    }
  }

  /* --------------------------------------------------------------- links */

  function repoBase() {
    // Works on <user>.github.io/<repo>/ and on a custom domain.
    var owner = "KM101304";
    var repo = "Vancouver-house-events";
    var match = window.location.hostname.match(/^([^.]+)\.github\.io$/i);
    if (match) {
      owner = match[1];
      var segment = window.location.pathname.split("/").filter(Boolean)[0];
      if (segment) repo = segment;
    }
    return "https://github.com/" + owner + "/" + repo;
  }

  function submitUrl() {
    return repoBase() + "/issues/new?template=submit-event.yml";
  }

  /* -------------------------------------------------------------- events */

  function resetFilters() {
    state.when = "all";
    state.genres.clear();
    state.venue = "";
    state.afterhours = false;
    state.query = "";
    $("#search").value = "";
    role("afterhours-filter").checked = false;
    syncWhenChips();
    renderFilters();
    renderListings();
  }

  function syncWhenChips() {
    Array.prototype.forEach.call(role("when-filters").children, function (chip) {
      var active = chip.dataset.when === state.when;
      chip.classList.toggle("is-active", active);
      chip.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function updateResetVisibility() {
    var dirty = state.when !== "all" || state.genres.size > 0 || state.venue ||
      state.afterhours || state.query.trim() !== "";
    role("reset").hidden = !dirty;
  }

  function bind() {
    var search = $("#search");
    var debounce;
    search.addEventListener("input", function () {
      clearTimeout(debounce);
      debounce = setTimeout(function () {
        state.query = search.value;
        renderListings();
      }, 120);
    });

    Array.prototype.forEach.call(role("when-filters").children, function (chip) {
      chip.addEventListener("click", function () {
        state.when = chip.dataset.when;
        syncWhenChips();
        renderListings();
      });
    });

    role("venue-filter").addEventListener("change", function (e) {
      state.venue = e.target.value;
      renderListings();
    });

    role("afterhours-filter").addEventListener("change", function (e) {
      state.afterhours = e.target.checked;
      renderListings();
    });

    role("reset").addEventListener("click", resetFilters);

    role("submit-link").href = submitUrl();
    role("repo-link").href = repoBase();

    var clock = role("clock");
    var tick = function () {
      clock.textContent = fmt(new Date(), { hour: "2-digit", minute: "2-digit", hour12: false });
    };
    tick();
    setInterval(tick, 30000);
  }

  /* ---------------------------------------------------------------- boot */

  function showError() {
    var container = role("listings");
    container.textContent = "";
    var wrap = el("div", "state");
    wrap.appendChild(el("h2", "state__title", "Couldn't load listings"));
    wrap.appendChild(el("p", "state__body", "The data feed didn't come back. Refresh, or read it directly at data/events.json."));
    container.appendChild(wrap);
    $(".listings").setAttribute("aria-busy", "false");
  }

  function boot() {
    bind();

    fetch(DATA_URL, { cache: "no-cache" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (payload) {
        state.events = (payload.events || []).filter(function (e) { return e && e.title && e.start; });
        state.meta = payload.meta || null;
        renderTallies();
        renderFilters();
        renderVenues();
        renderSources();
        renderListings();
      })
      .catch(function (err) {
        console.error("failed to load listings", err);
        showError();
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
