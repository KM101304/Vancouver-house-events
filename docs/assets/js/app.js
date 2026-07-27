/* After Dark — listings client.
 *
 * Event data comes from third-party APIs, so nothing from the feed is ever
 * interpolated into innerHTML. Text goes through textContent and URLs are
 * scheme-checked before they reach an href.
 */
(function () {
  "use strict";

  var DATA_URL = "data/events.json";
  var TZ = "America/Vancouver";

  // The night axis. Fixed rather than fitted to the data, so every night is
  // directly comparable and the 03:00 line always sits in the same place.
  var AXIS_START = 19 * 60;          // 19:00
  var AXIS_END = 32 * 60;            // 08:00 next day
  var AXIS_SPAN = AXIS_END - AXIS_START;
  var LAST_CALL = 27 * 60;           // 03:00 — BC last call
  var DEFAULT_RUN = 5 * 60;          // assumed length when a feed omits the end
  var PLOT_NIGHTS = 14;              // nights drawn before the plot stops being scannable

  // Fixed hue order, assigned to the entity. Never cycled, never re-assigned
  // when a filter changes the visible set.
  var HUES = {
    techno: "--g-techno", house: "--g-house", garage: "--g-garage",
    disco: "--g-disco", dnb: "--g-dnb", trance: "--g-trance", breaks: "--g-breaks"
  };

  var state = {
    events: [], meta: null,
    when: "all", genres: new Set(), venue: "", afterhours: false, query: ""
  };

  var $ = function (s) { return document.querySelector(s); };
  var role = function (n) { return document.querySelector('[data-role="' + n + '"]'); };

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
    } catch (err) { return null; }
  }

  function parseDate(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }

  function fmt(date, options) {
    options.timeZone = TZ;
    return new Intl.DateTimeFormat("en-CA", options).format(date);
  }

  function ymd(date) {
    var p = new Intl.DateTimeFormat("en-CA", {
      timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit"
    }).formatToParts(date).reduce(function (a, x) { a[x.type] = x.value; return a; }, {});
    return p.year + "-" + p.month + "-" + p.day;
  }

  // Anything before 6am belongs to the night before.
  function nightKey(date) { return ymd(new Date(date.getTime() - 6 * 3600 * 1000)); }
  function currentNight() { return nightKey(new Date()); }

  function clock(date) { return fmt(date, { hour: "2-digit", minute: "2-digit", hour12: false }); }

  function hueVar(genres) {
    for (var i = 0; i < (genres || []).length; i++) {
      if (HUES[genres[i]]) return "var(" + HUES[genres[i]] + ")";
    }
    return "var(--g-other)";
  }

  // Minutes from the start of the night's axis. An event's own clock time is
  // relative to its night, so a 02:00 set sits at 26*60, not 2*60.
  function axisMinutes(date, night) {
    var minutes = date.getHours() * 60 + date.getMinutes();
    // Recompute in Vancouver time rather than the viewer's locale.
    var parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false
    }).formatToParts(date).reduce(function (a, x) { a[x.type] = x.value; return a; }, {});
    minutes = parseInt(parts.hour, 10) * 60 + parseInt(parts.minute, 10);
    if (ymd(date) !== night) minutes += 24 * 60;   // rolled past midnight
    return minutes;
  }

  /* ------------------------------------------------------------ filtering */

  function matchesQuery(event, query) {
    if (!query) return true;
    var hay = [event.title, event.venue, event.promoter,
      (event.artists || []).join(" "), (event.genres || []).join(" ")].join(" ").toLowerCase();
    return query.split(/\s+/).every(function (t) { return hay.indexOf(t) !== -1; });
  }

  function withinWhen(event, when) {
    if (when === "all") return true;
    var tonight = currentNight();
    if (when === "tonight") return event.night === tonight;

    var days = Math.round(
      (new Date(event.night + "T12:00:00") - new Date(tonight + "T12:00:00")) / 86400000
    );
    if (days < 0) return false;
    if (when === "week") return days <= 6;
    if (when === "weekend") {
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
      if (state.genres.size && !(event.genres || []).some(function (g) { return state.genres.has(g); })) return false;
      return matchesQuery(event, query);
    });
  }

  function groupByNight(events) {
    var groups = [], index = {};
    events.forEach(function (event) {
      if (!index[event.night]) {
        index[event.night] = { night: event.night, events: [] };
        groups.push(index[event.night]);
      }
      index[event.night].events.push(event);
    });
    return groups;
  }

  /* ----------------------------------------------------------------- plot */

  // Place each bar in the first lane where it doesn't overlap something
  // already there, so a busy Saturday stacks instead of colliding.
  function packLanes(items) {
    var lanes = [];
    items.forEach(function (item) {
      for (var i = 0; i < lanes.length; i++) {
        if (item.from >= lanes[i]) { item.lane = i; lanes[i] = item.to; return; }
      }
      item.lane = lanes.length;
      lanes.push(item.to);
    });
    return lanes.length;
  }

  function geometry(event) {
    var start = parseDate(event.start);
    if (!start) return null;
    var from = axisMinutes(start, event.night);

    var end = parseDate(event.end);
    var open = !end;
    var to = end ? axisMinutes(end, event.night) : from + DEFAULT_RUN;
    if (to <= from) to = from + DEFAULT_RUN;

    var clipped = from < AXIS_START;
    from = Math.max(from, AXIS_START);
    to = Math.min(to, AXIS_END);
    if (to <= from) return null;

    return { event: event, from: from, to: to, open: open, clipped: clipped };
  }

  function pct(minutes) { return ((minutes - AXIS_START) / AXIS_SPAN) * 100; }

  function renderAxis() {
    var axis = el("div", "axis");
    axis.appendChild(el("div", "axis__corner", "Night"));
    var track = el("div", "axis__track");
    for (var m = AXIS_START + 60; m < AXIS_END; m += 120) {
      var tick = el("div", "axis__tick", String((m / 60) % 24).padStart(2, "0"));
      tick.style.left = pct(m) + "%";
      track.appendChild(tick);
    }
    // Last call is stated once, on the axis. Repeating it down every row
    // buried the bars it was meant to give context to.
    var call = el("div", "axis__call", "last call");
    call.style.left = pct(LAST_CALL) + "%";
    track.appendChild(call);
    axis.appendChild(track);
    return axis;
  }

  function renderPlot(events) {
    var host = role("plot");
    host.textContent = "";

    if (!events.length) {
      host.appendChild(el("p", "plot__empty", "Nothing to plot for these filters."));
      role("plot-hint").hidden = true;
      return;
    }
    role("plot-hint").hidden = false;
    host.appendChild(renderAxis());

    var tonight = currentNight();
    var nowMinutes = axisMinutes(new Date(), tonight);

    // The plot is for reading a stretch of nights at a glance; past a couple of
    // weeks it stops being scannable. The full set always stays in the list.
    var all = groupByNight(events);
    var shown = all.slice(0, PLOT_NIGHTS);

    shown.forEach(function (group) {
      var isTonight = group.night === tonight;
      var row = el("div", "night" + (isTonight ? " night--tonight" : ""));

      var date = new Date(group.night + "T12:00:00");
      var label = el("div", "night__label");
      label.appendChild(el("span", "night__day", isTonight ? "Tonight" : fmt(date, { weekday: "short" })));
      label.appendChild(el("span", "night__date", fmt(date, { month: "short", day: "numeric" })));
      row.appendChild(label);

      var track = el("div", "night__track");
      // One gridline every two hours, matching the axis ticks.
      track.style.setProperty("--hour-step", (120 / AXIS_SPAN) * 100 + "%");

      var last = el("div", "marker");
      last.style.left = pct(LAST_CALL) + "%";
      track.appendChild(last);

      if (isTonight && nowMinutes > AXIS_START && nowMinutes < AXIS_END) {
        var now = el("div", "marker marker--now");
        now.style.left = pct(nowMinutes) + "%";
        now.appendChild(el("span", "marker__flag", "now"));
        track.appendChild(now);
      }

      var bars = group.events.map(geometry).filter(Boolean);
      bars.sort(function (a, b) { return a.from - b.from || a.to - b.to; });
      var lanes = packLanes(bars);

      bars.forEach(function (item) {
        var bar = el("button", "bar" + (item.open ? " bar--open" : "") + (item.clipped ? " bar--clipped" : ""));
        bar.type = "button";
        bar.style.setProperty("--from", pct(item.from) + "%");
        bar.style.setProperty("--span", ((item.to - item.from) / AXIS_SPAN) * 100 + "%");
        bar.style.setProperty("--lane", item.lane);
        bar.style.setProperty("--hue", hueVar(item.event.genres));
        bar.dataset.id = item.event.id;

        // Label only when the bar can hold one; the tooltip covers the rest.
        if ((item.to - item.from) / AXIS_SPAN > 0.14) bar.textContent = item.event.title;
        bar.setAttribute("aria-label",
          item.event.title + ", " + item.event.venue + ", from " +
          clock(parseDate(item.event.start)) + ", " + (item.event.genres || []).join(" ") );

        bar.addEventListener("mouseenter", function (e) { showTip(item, e); });
        bar.addEventListener("mousemove", function (e) { positionTip(e); });
        bar.addEventListener("mouseleave", hideTip);
        bar.addEventListener("focus", function (e) { showTip(item, e); });
        bar.addEventListener("blur", hideTip);
        bar.addEventListener("click", function () { jumpToGig(item.event.id); });

        track.appendChild(bar);
      });

      track.style.minHeight = (lanes * 1.75 + 0.8) + "rem";
      row.appendChild(track);
      host.appendChild(row);
    });

    // Never truncate silently — say what was left off and where it went.
    if (all.length > shown.length) {
      var rest = all.length - shown.length;
      host.appendChild(el("p", "plot__more",
        "+ " + rest + (rest === 1 ? " more night" : " more nights") + " further out — listed in full below."));
    }
  }

  /* -------------------------------------------------------------- tooltip */

  function showTip(item, event) {
    var tip = role("tip");
    tip.textContent = "";
    var e = item.event;

    tip.appendChild(el("div", "tip__title", e.title));

    var when = clock(parseDate(e.start));
    when += e.end ? "–" + clock(parseDate(e.end)) : " · end time not published";
    tip.appendChild(el("div", "tip__meta", when));
    if (e.venue) tip.appendChild(el("div", "tip__meta", e.venue));

    var genres = (e.genres || []);
    if (genres.length) {
      var line = el("div", "tip__genre");
      var swatch = el("span", "tip__swatch");
      swatch.style.background = hueVar(genres);
      line.appendChild(swatch);
      line.appendChild(el("span", "tip__meta", genres.join(" · ")));
      tip.appendChild(line);
    }

    tip.hidden = false;
    positionTip(event);
    highlight(e.id, true);
  }

  function positionTip(event) {
    var tip = role("tip");
    if (tip.hidden) return;
    var box = tip.getBoundingClientRect();
    var x = (event.clientX || 0) + 14;
    var y = (event.clientY || 0) + 16;
    // Keep it on screen near the right and bottom edges.
    if (x + box.width > window.innerWidth - 8) x = window.innerWidth - box.width - 8;
    if (y + box.height > window.innerHeight - 8) y = (event.clientY || 0) - box.height - 12;
    tip.style.left = Math.max(8, x) + "px";
    tip.style.top = Math.max(8, y) + "px";
  }

  function hideTip() {
    role("tip").hidden = true;
    document.querySelectorAll(".is-lit").forEach(function (n) { n.classList.remove("is-lit"); });
  }

  function highlight(id, on) {
    var row = document.querySelector('.gig[data-id="' + CSS.escape(id) + '"]');
    if (row) row.classList.toggle("is-lit", on);
  }

  function jumpToGig(id) {
    var row = document.querySelector('.gig[data-id="' + CSS.escape(id) + '"]');
    if (!row) return;
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("is-lit");
    setTimeout(function () { row.classList.remove("is-lit"); }, 1600);
  }

  /* -------------------------------------------------------------- listing */

  function icsFor(event) {
    var start = parseDate(event.start);
    var end = parseDate(event.end) || new Date(start.getTime() + DEFAULT_RUN * 60000);
    var st = function (d) { return d.toISOString().replace(/[-:]/g, "").split(".")[0] + "Z"; };
    var esc = function (s) { return String(s || "").replace(/([,;\\])/g, "\\$1").replace(/\n/g, "\\n"); };
    var summary = event.title;
    if (event.artists && event.artists.length) summary += " — " + event.artists.slice(0, 4).join(", ");
    return [
      "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//after-dark//EN", "BEGIN:VEVENT",
      "UID:" + event.id + "@vancouver-house-events",
      "DTSTAMP:" + st(new Date()), "DTSTART:" + st(start), "DTEND:" + st(end),
      "SUMMARY:" + esc(summary), "LOCATION:" + esc(event.venue),
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

  function renderGig(event) {
    var row = el("article", "gig");
    row.dataset.id = event.id;

    var start = parseDate(event.start);
    var time = el("div", "gig__time");
    time.appendChild(el("span", null, start ? clock(start) : "--:--"));
    var notes = [];
    // A 02:30 set listed under Thursday actually happens on Friday morning.
    if (start && ymd(start) !== event.night) notes.push(fmt(start, { weekday: "short" }) + " am");
    if (event.end) {
      var end = parseDate(event.end);
      if (end) notes.push("til " + clock(end));
    }
    if (notes.length) time.appendChild(el("small", null, notes.join(" · ")));
    row.appendChild(time);

    var main = el("div", "gig__main");
    var title = el("h3", "gig__title");
    var href = safeUrl(event.url) || safeUrl(event.ticketUrl);
    if (href) {
      var link = el("a", null, event.title);
      link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer";
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
      var chip = el("span", "chip");
      var swatch = el("span", "chip__swatch");
      swatch.style.background = hueVar([genre]);
      chip.appendChild(swatch);
      chip.appendChild(el("span", null, genre));
      facts.appendChild(chip);
    });
    if (event.afterhours) facts.appendChild(el("span", "flag flag--afterhours", "afterhours"));
    if (event.stale) {
      var stale = el("span", "flag flag--stale", "unconfirmed");
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
    var ticket = safeUrl(event.ticketUrl);
    if (ticket) {
      var buy = el("a", "btn", "Tickets");
      buy.href = ticket; buy.target = "_blank"; buy.rel = "noopener noreferrer";
      aside.appendChild(buy);
    }
    var add = el("button", "ghost", "Calendar");
    add.type = "button";
    add.setAttribute("aria-label", "Add " + event.title + " to calendar");
    add.addEventListener("click", function () { downloadIcs(event); });
    aside.appendChild(add);
    row.appendChild(aside);

    return row;
  }

  function renderListing(events) {
    var host = role("listings");
    host.textContent = "";

    if (!events.length) {
      host.appendChild(emptyState());
      $(".listing").setAttribute("aria-busy", "false");
      return;
    }

    var tonight = currentNight();
    groupByNight(events).forEach(function (group) {
      var date = new Date(group.night + "T12:00:00");
      var isTonight = group.night === tonight;
      var head = el("div", "daybreak" + (isTonight ? " daybreak--tonight" : ""));
      head.appendChild(el("h2", "daybreak__day",
        (isTonight ? "Tonight" : fmt(date, { weekday: "long" })) + " · " + fmt(date, { month: "short", day: "numeric" })));
      head.appendChild(el("span", "daybreak__count",
        group.events.length + (group.events.length === 1 ? " party" : " parties")));
      host.appendChild(head);
      group.events.forEach(function (event) { host.appendChild(renderGig(event)); });
    });

    $(".listing").setAttribute("aria-busy", "false");
  }

  function emptyState() {
    var wrap = el("div", "state");
    if (state.events.length) {
      wrap.appendChild(el("h2", "state__title", "Nothing matches"));
      wrap.appendChild(el("p", "state__body", "No nights fit those filters. Try a wider date range or fewer genres."));
      var clear = el("button", "ghost", "Reset filters");
      clear.type = "button";
      clear.addEventListener("click", resetFilters);
      wrap.appendChild(clear);
      return wrap;
    }
    wrap.appendChild(el("h2", "state__title", "Nothing listed yet"));
    var failed = state.meta && (state.meta.sources || []).filter(function (s) { return s.status === "failed"; });
    wrap.appendChild(el("p", "state__body", (failed && failed.length)
      ? "The feeds came back empty on the last refresh. They retry automatically every six hours."
      : "The first feed refresh hasn't run yet. Once it does, the city shows up here."));
    var submit = el("a", "btn", "Add a night");
    submit.href = submitUrl(); submit.target = "_blank"; submit.rel = "noopener noreferrer";
    wrap.appendChild(submit);
    return wrap;
  }

  /* --------------------------------------------------------------- chrome */

  function renderLegend() {
    var counts = {};
    state.events.forEach(function (e) {
      (e.genres || []).forEach(function (g) { counts[g] = (counts[g] || 0) + 1; });
    });

    var box = role("genre-filters");
    box.textContent = "";
    box.classList.toggle("has-selection", state.genres.size > 0);

    Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; }).forEach(function (genre) {
      var on = state.genres.has(genre);
      var key = el("button", "key" + (on ? " is-on" : ""));
      key.type = "button";
      key.setAttribute("aria-pressed", on ? "true" : "false");
      var swatch = el("span", "key__swatch");
      swatch.style.background = hueVar([genre]);
      key.appendChild(swatch);
      key.appendChild(document.createTextNode(genre));
      key.appendChild(el("span", "key__count", counts[genre]));
      key.addEventListener("click", function () {
        if (on) state.genres.delete(genre); else state.genres.add(genre);
        renderLegend();
        draw();
      });
      box.appendChild(key);
    });
  }

  function renderVenuePicker() {
    var counts = {};
    state.events.forEach(function (e) { if (e.venue) counts[e.venue] = (counts[e.venue] || 0) + 1; });
    var select = role("venue-filter");
    select.textContent = "";
    select.appendChild(new Option("Every room", ""));
    Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a] || a.localeCompare(b); })
      .forEach(function (name) {
        var option = new Option(name + " (" + counts[name] + ")", name);
        if (name === state.venue) option.selected = true;
        select.appendChild(option);
      });
  }

  function renderRooms() {
    var counts = {};
    state.events.forEach(function (e) { if (e.venue) counts[e.venue] = (counts[e.venue] || 0) + 1; });
    var list = role("venue-list");
    list.textContent = "";
    var names = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a] || a.localeCompare(b); }).slice(0, 12);
    if (!names.length) { list.appendChild(el("li", "room", "No rooms listed yet.")); return; }
    names.forEach(function (name) {
      var item = el("li", "room");
      var button = el("button", null, name);
      button.type = "button";
      button.addEventListener("click", function () {
        state.venue = state.venue === name ? "" : name;
        state.when = "all";
        syncWhen();
        renderVenuePicker();
        draw();
        role("plot-scroll").scrollIntoView({ behavior: "smooth", block: "start" });
      });
      item.appendChild(button);
      item.appendChild(el("span", null, counts[name]));
      list.appendChild(item);
    });
  }

  function renderSources() {
    var list = role("source-list");
    list.textContent = "";
    ((state.meta && state.meta.sources) || []).forEach(function (source) {
      var item = el("li", "source source--" + source.status);
      item.appendChild(el("span", "source__dot"));
      item.appendChild(el("span", null, source.label || source.source));
      if (source.detail) item.title = source.detail;
      list.appendChild(item);
    });

    if (state.meta && state.meta.updated) {
      var updated = parseDate(state.meta.updated);
      if (updated) {
        role("updated").textContent = fmt(updated, {
          weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false
        });
      }
    }
  }

  /* ---------------------------------------------------------------- links */

  function repoBase() {
    var owner = "KM101304", repo = "Vancouver-house-events";
    var match = window.location.hostname.match(/^([^.]+)\.github\.io$/i);
    if (match) {
      owner = match[1];
      var segment = window.location.pathname.split("/").filter(Boolean)[0];
      if (segment) repo = segment;
    }
    return "https://github.com/" + owner + "/" + repo;
  }
  function submitUrl() { return repoBase() + "/issues/new?template=submit-event.yml"; }

  /* --------------------------------------------------------------- events */

  function draw() {
    var events = visibleEvents();
    renderPlot(events);
    renderListing(events);
    var dirty = state.when !== "all" || state.genres.size > 0 || state.venue ||
      state.afterhours || state.query.trim() !== "";
    role("reset").hidden = !dirty;
  }

  function resetFilters() {
    state.when = "all"; state.genres.clear(); state.venue = "";
    state.afterhours = false; state.query = "";
    $("#search").value = "";
    role("afterhours-filter").checked = false;
    syncWhen();
    renderLegend();
    renderVenuePicker();
    draw();
  }

  function syncWhen() {
    Array.prototype.forEach.call(role("when-filters").children, function (seg) {
      var active = seg.dataset.when === state.when;
      seg.classList.toggle("is-active", active);
      seg.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function bind() {
    var search = $("#search"), debounce;
    search.addEventListener("input", function () {
      clearTimeout(debounce);
      debounce = setTimeout(function () { state.query = search.value; draw(); }, 120);
    });

    Array.prototype.forEach.call(role("when-filters").children, function (seg) {
      seg.addEventListener("click", function () {
        state.when = seg.dataset.when;
        syncWhen();
        draw();
      });
    });

    role("venue-filter").addEventListener("change", function (e) { state.venue = e.target.value; draw(); });
    role("afterhours-filter").addEventListener("change", function (e) { state.afterhours = e.target.checked; draw(); });
    role("reset").addEventListener("click", resetFilters);
    role("submit-link").href = submitUrl();
    role("repo-link").href = repoBase();
    window.addEventListener("scroll", hideTip, { passive: true });
  }

  function showError() {
    var host = role("listings");
    host.textContent = "";
    var wrap = el("div", "state");
    wrap.appendChild(el("h2", "state__title", "Couldn't load listings"));
    wrap.appendChild(el("p", "state__body", "The data feed didn't come back. Refresh, or read it directly at data/events.json."));
    host.appendChild(wrap);
    role("plot-section").hidden = true;
    $(".listing").setAttribute("aria-busy", "false");
  }

  function boot() {
    bind();
    fetch(DATA_URL, { cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (payload) {
        state.events = (payload.events || []).filter(function (e) { return e && e.title && e.start; });
        state.meta = payload.meta || null;
        renderSources();
        renderLegend();
        renderVenuePicker();
        renderRooms();
        draw();
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
