/* Afters — listings client.
 *
 * Event data comes from third-party APIs, so nothing from the feed is ever
 * interpolated into innerHTML. Text goes through textContent and URLs are
 * scheme-checked before they reach an href or a background-image.
 */
(function () {
  "use strict";

  var DATA_URL = "data/events.json";
  var TZ = "America/Vancouver";

  var state = {
    events: [], meta: null,
    when: "all", genres: new Set(), venue: "", query: ""
  };

  var WHENS = [
    { key: "all", label: "All" },
    { key: "tonight", label: "Tonight" },
    { key: "weekend", label: "This weekend" },
    { key: "week", label: "Next 7 nights" }
  ];

  var $ = function (s) { return document.querySelector(s); };
  var role = function (n) { return document.querySelector('[data-role="' + n + '"]'); };

  /* --------------------------------------------------------------- utils */

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // Only http(s) survives; blocks javascript: and data: URLs that could ride
  // in on a compromised or sloppy upstream feed.
  function safeUrl(v) {
    if (!v) return null;
    try {
      var u = new URL(v, window.location.href);
      return (u.protocol === "http:" || u.protocol === "https:") ? u.href : null;
    } catch (e) { return null; }
  }

  function parseDate(iso) { var d = new Date(iso); return isNaN(d.getTime()) ? null : d; }

  function fmt(date, opts) { opts.timeZone = TZ; return new Intl.DateTimeFormat("en-CA", opts).format(date); }

  function ymd(date) {
    var p = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" })
      .formatToParts(date).reduce(function (a, x) { a[x.type] = x.value; return a; }, {});
    return p.year + "-" + p.month + "-" + p.day;
  }

  // Anything before 6am belongs to the night before.
  function currentNight() { return ymd(new Date(Date.now() - 6 * 3600 * 1000)); }
  function clock(d) { return fmt(d, { hour: "2-digit", minute: "2-digit", hour12: false }); }

  function daysFromTonight(night) {
    return Math.round((new Date(night + "T12:00:00") - new Date(currentNight() + "T12:00:00")) / 86400000);
  }

  // Stable hue per event so generated artwork never reshuffles between loads.
  // Constrained to the warm end so generated artwork sits with the accent
  // instead of throwing a random neon into the grid.
  function hueOf(id) {
    var h = 0;
    for (var i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) % 90;
    return (h + 330) % 360;
  }

  function initials(title) {
    var words = String(title || "?").replace(/[^A-Za-z0-9 ]/g, " ").trim().split(/\s+/);
    return ((words[0] || "?")[0] + (words[1] ? words[1][0] : "")).toUpperCase();
  }

  /* ----------------------------------------------------------- filtering */

  function matches(e, q) {
    if (!q) return true;
    var hay = [e.title, e.venue, e.promoter, (e.artists || []).join(" "), (e.genres || []).join(" ")]
      .join(" ").toLowerCase();
    return q.split(/\s+/).every(function (t) { return hay.indexOf(t) !== -1; });
  }

  function inWhen(e, when) {
    if (when === "all") return true;
    if (when === "tonight") return e.night === currentNight();
    var d = daysFromTonight(e.night);
    if (d < 0) return false;
    if (when === "week") return d <= 6;
    if (when === "weekend") {
      var dow = fmt(new Date(e.night + "T12:00:00"), { weekday: "short" });
      return d <= 7 && (dow === "Fri" || dow === "Sat");
    }
    return true;
  }

  function visible() {
    var q = state.query.trim().toLowerCase();
    return state.events.filter(function (e) {
      if (!inWhen(e, state.when)) return false;
      if (state.venue && e.venue !== state.venue) return false;
      if (state.genres.size && !(e.genres || []).some(function (g) { return state.genres.has(g); })) return false;
      return matches(e, q);
    });
  }

  /* ------------------------------------------------------------ calendar */

  function downloadIcs(e) {
    var start = parseDate(e.start);
    var end = parseDate(e.end) || new Date(start.getTime() + 5 * 3600 * 1000);
    var st = function (d) { return d.toISOString().replace(/[-:]/g, "").split(".")[0] + "Z"; };
    var esc = function (s) { return String(s || "").replace(/([,;\\])/g, "\\$1").replace(/\n/g, "\\n"); };
    var summary = e.title + (e.artists && e.artists.length ? " — " + e.artists.slice(0, 4).join(", ") : "");
    var body = [
      "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//after-dark//EN", "BEGIN:VEVENT",
      "UID:" + e.id + "@vancouver-house-events",
      "DTSTAMP:" + st(new Date()), "DTSTART:" + st(start), "DTEND:" + st(end),
      "SUMMARY:" + esc(summary), "LOCATION:" + esc(e.venue),
      "DESCRIPTION:" + esc(e.ticketUrl ? "Tickets: " + e.ticketUrl : ""),
      "END:VEVENT", "END:VCALENDAR"
    ].join("\r\n");

    var url = URL.createObjectURL(new Blob([body], { type: "text/calendar;charset=utf-8" }));
    var a = el("a");
    a.href = url;
    a.download = (e.title || "event").replace(/[^a-z0-9]+/gi, "-").toLowerCase() + ".ics";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  /* -------------------------------------------------------------- render */

  function renderCard(e, wide) {
    var card = el("article", "card" + (wide ? " card--wide" : ""));
    var href = safeUrl(e.url) || safeUrl(e.ticketUrl);
    var art = el(href ? "a" : "div", "card__art");
    if (href) { art.href = href; art.target = "_blank"; art.rel = "noopener noreferrer"; }

    var img = safeUrl(e.image);
    if (img) {
      var picture = el("img");
      picture.src = img;
      picture.alt = "";
      picture.loading = "lazy";
      picture.decoding = "async";
      // A dead flyer URL shouldn't leave a broken-image hole in the grid.
      picture.addEventListener("error", function () {
        picture.remove();
        art.classList.add("card__art--none");
        art.style.setProperty("--h", hueOf(e.id));
        art.insertBefore(el("span", "card__mono", initials(e.title)), art.firstChild);
      });
      art.appendChild(picture);
    } else {
      art.classList.add("card__art--none");
      art.style.setProperty("--h", hueOf(e.id));
      art.appendChild(el("span", "card__mono", initials(e.title)));
    }

    var start = parseDate(e.start);
    if (start) art.appendChild(el("span", "card__time", clock(start)));
    if (e.afterhours) art.appendChild(el("span", "card__tag card__tag--after", "afters"));
    else if (e.stale) {
      var stale = el("span", "card__tag", "unconfirmed");
      stale.title = "This source was unreachable on the last refresh — check with the venue.";
      art.appendChild(stale);
    }
    card.appendChild(art);

    var body = el("div", "card__body");
    var title = el("h3", "card__title");
    if (href) {
      var link = el("a", null, e.title);
      link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer";
      title.appendChild(link);
    } else { title.textContent = e.title; }
    body.appendChild(title);

    if (e.venue) body.appendChild(el("p", "card__venue", e.venue));
    if (e.artists && e.artists.length) body.appendChild(el("p", "card__line", e.artists.join(", ")));
    else if (e.promoter) body.appendChild(el("p", "card__line", e.promoter));

    var foot = el("div", "card__foot");
    if (e.genres && e.genres.length) foot.appendChild(el("span", "genre", e.genres[0]));
    if (e.price && e.price.min != null) {
      var price = "$" + Math.round(e.price.min);
      if (e.price.max != null && e.price.max > e.price.min) price += "–" + Math.round(e.price.max);
      foot.appendChild(el("span", "card__price", price));
    }

    var ticket = safeUrl(e.ticketUrl);
    if (ticket) {
      var buy = el("a", "btn btn--solid", "Tickets");
      buy.href = ticket; buy.target = "_blank"; buy.rel = "noopener noreferrer";
      foot.appendChild(buy);
    } else {
      var add = el("button", "btn", "Save");
      add.type = "button";
      add.setAttribute("aria-label", "Add " + e.title + " to calendar");
      add.addEventListener("click", function () { downloadIcs(e); });
      foot.appendChild(add);
    }
    body.appendChild(foot);
    card.appendChild(body);
    return card;
  }

  function renderFeed() {
    var host = role("feed");
    var events = visible();
    host.textContent = "";

    if (!events.length) { host.appendChild(emptyNote()); $(".feed").setAttribute("aria-busy", "false"); return; }

    var tonight = currentNight();
    var groups = [], index = {};
    events.forEach(function (e) {
      if (!index[e.night]) { index[e.night] = { night: e.night, events: [] }; groups.push(index[e.night]); }
      index[e.night].events.push(e);
    });

    groups.forEach(function (g, i) {
      var date = new Date(g.night + "T12:00:00");
      var isTonight = g.night === tonight;
      var d = daysFromTonight(g.night);

      var head = el("div", "day" + (isTonight ? " day--tonight" : ""));
      var name = isTonight ? "Tonight" : d === 1 ? "Tomorrow" : fmt(date, { weekday: "long" });
      head.appendChild(el("h2", "day__name", name));
      head.appendChild(el("span", "day__date", fmt(date, { month: "short", day: "numeric" })));
      head.appendChild(el("span", "day__n", g.events.length + (g.events.length === 1 ? " event" : " events")));
      host.appendChild(head);

      // Tonight (and tomorrow, when tonight is over) gets the bigger rhythm.
      var feature = isTonight || (i === 0 && d <= 1);
      // A one-event Tuesday shouldn't occupy a full row of empty columns.
      var wide = !feature && g.events.length <= 2;
      var grid = el("div", "grid" + (feature ? " grid--feature" : wide ? " grid--wide" : ""));
      g.events.forEach(function (e) { grid.appendChild(renderCard(e, wide)); });
      host.appendChild(grid);
    });

    $(".feed").setAttribute("aria-busy", "false");
  }

  function emptyNote() {
    var wrap = el("p", "note");
    if (state.events.length) {
      wrap.appendChild(el("strong", null, "Nothing matches"));
      wrap.appendChild(document.createTextNode("No events fit those filters. Try a wider date range or fewer genres."));
      var clear = el("button", "btn", "Clear filters");
      clear.type = "button";
      clear.addEventListener("click", reset);
      wrap.appendChild(clear);
      return wrap;
    }
    wrap.appendChild(el("strong", null, "Nothing listed yet"));
    var failed = state.meta && (state.meta.sources || []).filter(function (s) { return s.status === "failed"; });
    wrap.appendChild(document.createTextNode((failed && failed.length)
      ? "The feeds came back empty on the last refresh. They retry automatically every six hours."
      : "The first feed refresh hasn't run yet. Once it does, the city shows up here."));
    var submit = el("a", "btn btn--solid", "Add an event");
    submit.href = submitUrl(); submit.target = "_blank"; submit.rel = "noopener noreferrer";
    wrap.appendChild(submit);
    return wrap;
  }

  function renderPills() {
    var when = role("when");
    when.textContent = "";
    WHENS.forEach(function (w) {
      var b = el("button", "pill" + (state.when === w.key ? " is-on" : ""), w.label);
      b.type = "button";
      b.setAttribute("aria-pressed", state.when === w.key ? "true" : "false");
      b.addEventListener("click", function () { state.when = w.key; renderPills(); renderFeed(); dirty(); });
      when.appendChild(b);
    });

    var counts = {};
    state.events.forEach(function (e) { (e.genres || []).forEach(function (g) { counts[g] = (counts[g] || 0) + 1; }); });
    var box = role("genres");
    box.textContent = "";
    Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; }).forEach(function (g) {
      var on = state.genres.has(g);
      var b = el("button", "pill" + (on ? " is-on" : ""));
      b.type = "button";
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.appendChild(document.createTextNode(g));
      b.appendChild(el("span", "pill__n", counts[g]));
      b.addEventListener("click", function () {
        if (on) state.genres.delete(g); else state.genres.add(g);
        renderPills(); renderFeed(); dirty();
      });
      box.appendChild(b);
    });
  }

  function renderStats() {
    var tonight = currentNight();
    role("stat-tonight").textContent =
      state.events.filter(function (e) { return e.night === tonight; }).length;
    role("stat-week").textContent =
      state.events.filter(function (e) { var d = daysFromTonight(e.night); return d >= 0 && d <= 6; }).length;
    var venues = {};
    state.events.forEach(function (e) { if (e.venue) venues[e.venue] = 1; });
    role("stat-venues").textContent = Object.keys(venues).length;
  }

  function renderRooms() {
    var counts = {};
    state.events.forEach(function (e) { if (e.venue) counts[e.venue] = (counts[e.venue] || 0) + 1; });
    var list = role("rooms");
    list.textContent = "";
    Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a] || a.localeCompare(b); })
      .slice(0, 10).forEach(function (name) {
        var li = el("li");
        var b = el("button", null, name);
        b.type = "button";
        b.addEventListener("click", function () {
          state.venue = state.venue === name ? "" : name;
          state.when = "all";
          renderPills(); renderFeed(); dirty();
          document.getElementById("feed").scrollIntoView({ behavior: "smooth", block: "start" });
        });
        li.appendChild(b);
        li.appendChild(el("span", null, counts[name]));
        list.appendChild(li);
      });
  }

  function renderSources() {
    var list = role("sources");
    list.textContent = "";
    ((state.meta && state.meta.sources) || []).forEach(function (s) {
      var li = el("li", "source source--" + s.status);
      li.appendChild(el("span", "source__dot"));
      li.appendChild(el("span", null, s.label || s.source));
      if (s.detail) li.title = s.detail;
      list.appendChild(li);
    });
    if (state.meta && state.meta.updated) {
      var u = parseDate(state.meta.updated);
      if (u) role("updated").textContent = fmt(u, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
    }
  }

  /* --------------------------------------------------------------- links */

  function repoBase() {
    var owner = "KM101304", repo = "Vancouver-house-events";
    var m = window.location.hostname.match(/^([^.]+)\.github\.io$/i);
    if (m) {
      owner = m[1];
      var seg = window.location.pathname.split("/").filter(Boolean)[0];
      if (seg) repo = seg;
    }
    return "https://github.com/" + owner + "/" + repo;
  }
  function submitUrl() { return repoBase() + "/issues/new?template=submit-event.yml"; }

  /* -------------------------------------------------------------- events */

  function dirty() {
    role("reset").hidden = !(state.when !== "all" || state.genres.size || state.venue || state.query.trim());
  }

  function reset() {
    state.when = "all"; state.genres.clear(); state.venue = ""; state.query = "";
    $("#search").value = "";
    renderPills(); renderFeed(); dirty();
  }

  function boot() {
    var search = $("#search"), t;
    search.addEventListener("input", function () {
      clearTimeout(t);
      t = setTimeout(function () { state.query = search.value; renderFeed(); dirty(); }, 120);
    });
    role("reset").addEventListener("click", reset);
    role("submit").href = submitUrl();

    fetch(DATA_URL, { cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (p) {
        state.events = (p.events || []).filter(function (e) { return e && e.title && e.start; });
        state.meta = p.meta || null;
        renderStats(); renderPills(); renderRooms(); renderSources(); renderFeed();
      })
      .catch(function (err) {
        console.error("failed to load listings", err);
        var host = role("feed");
        host.textContent = "";
        var n = el("p", "note");
        n.appendChild(el("strong", null, "Couldn't load listings"));
        n.appendChild(document.createTextNode("The data feed didn't come back. Refresh, or read it directly at data/events.json."));
        host.appendChild(n);
        $(".feed").setAttribute("aria-busy", "false");
      });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
