(function () {
  "use strict";
  // The public job board. Read only: it calls /api/public/* and nothing else,
  // and loads no private script.

  var state = { tab: "today", query: "", postings: [], reportId: null };

  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function fmtDate(s) {
    if (!s) return "";
    var p = String(s).split("-");
    if (p.length !== 3) return String(s);
    return new Date(+p[0], +p[1] - 1, +p[2]).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  function fmtDateTime(s) {
    var d = new Date(s);
    if (!s || isNaN(d.getTime())) return "";
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "America/Chicago" }) +
      ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZone: "America/Chicago" }) + " Central";
  }

  // Today's date in Central time, as YYYY-MM-DD (en-CA formats dates that way).
  function centralToday() {
    return new Date().toLocaleDateString("en-CA", { timeZone: "America/Chicago" });
  }

  function safeUrl(u) {
    try {
      var x = new URL(String(u), window.location.href);
      return x.protocol === "https:" || x.protocol === "http:" ? x.href : null;
    } catch (e) {
      return null;
    }
  }

  function titleOf(p) { return p.title_en || p.title || ""; }

  // The API sends confirmed: false while a posting's status isn't confirmed yet.
  function isConfirmed(p) { return p.confirmed !== false; }

  function matches(p, q) {
    if (!q) return true;
    var haystack = [p.company_name, titleOf(p), p.title || "", (p.locations || []).join(" ")].join(" ").toLowerCase();
    return haystack.indexOf(q) !== -1;
  }

  // Splits postings into the "New today" and "Earlier" tabs.
  function splitByTab(postings, today) {
    return {
      today: postings.filter(function (p) { return p.first_seen === today; }),
      earlier: postings.filter(function (p) { return p.first_seen !== today; })
    };
  }

  function render() {
    var split = splitByTab(state.postings, centralToday());
    var todayRows = split.today;
    var earlierRows = split.earlier;
    $("nToday").textContent = todayRows.length;
    $("nEarlier").textContent = earlierRows.length;

    var q = state.query.toLowerCase();
    var rows = (state.tab === "today" ? todayRows : earlierRows).filter(function (p) { return matches(p, q); });

    var list = $("list");
    list.textContent = "";
    if (!rows.length) {
      var li = el("li");
      var box = el("div", "empty");
      if (q) box.appendChild(el("strong", null, "No postings match that search"));
      else if (state.tab === "today") box.appendChild(el("strong", null, "Nothing new yet today"));
      else box.appendChild(el("strong", null, "No earlier postings"));
      li.appendChild(box);
      list.appendChild(li);
      return;
    }

    rows.forEach(function (p) {
      var item = el("li", "item" + (state.tab === "today" ? " new" : ""));
      if (!isConfirmed(p)) item.appendChild(el("span", "badge unverified", "Not yet confirmed"));
      item.appendChild(el("h3", "co", p.company_name || ""));
      item.appendChild(el("div", "ti", titleOf(p)));
      var meta = el("div", "meta");
      var locations = (p.locations || []).join(", ");
      if (locations) meta.appendChild(el("span", null, locations));
      if (p.posted_date) meta.appendChild(el("span", null, "posted " + fmtDate(p.posted_date)));
      meta.appendChild(el("span", null, "found " + fmtDate(p.first_seen)));
      item.appendChild(meta);

      var actions = el("div", "actions");
      var href = safeUrl(p.url);
      if (href) {
        var a = el("a", "btn primary", "Open posting");
        a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
        actions.appendChild(a);
      }
      var report = el("button", "btn", "Report");
      report.type = "button";
      report.addEventListener("click", function () { openFeedback(p); });
      actions.appendChild(report);
      item.appendChild(actions);
      list.appendChild(item);
    });
  }

  function selectTab(t) {
    state.tab = t;
    $("tabToday").setAttribute("aria-selected", t === "today" ? "true" : "false");
    $("tabEarlier").setAttribute("aria-selected", t === "earlier" ? "true" : "false");
    render();
  }
  $("tabToday").addEventListener("click", function () { selectTab("today"); });
  $("tabEarlier").addEventListener("click", function () { selectTab("earlier"); });
  $("filter").addEventListener("input", function (e) { state.query = e.target.value.trim(); render(); });

  // -------------------------------------------------------------------
  // Feedback
  // -------------------------------------------------------------------

  function openFeedback(posting) {
    state.reportId = posting ? posting.id : null;
    $("fbPosting").hidden = !posting;
    $("fbPosting").textContent = posting ? "About: " + posting.company_name + ", " + titleOf(posting) : "";
    $("fbMsg").textContent = "";
    $("fbMessage").value = "";
    $("fbWebsite").value = "";
    $("fbKind").value = posting ? "dead_link" : "suggestion";
    var dialog = $("feedbackDialog");
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", "");
  }

  function closeFeedback() {
    var dialog = $("feedbackDialog");
    if (dialog.close) dialog.close(); else dialog.removeAttribute("open");
  }

  $("openFeedback").addEventListener("click", function () { openFeedback(null); });
  $("fbClose").addEventListener("click", closeFeedback);
  function feedbackError(message) {
    return String(message || "").trim() ? null : "Write a message first.";
  }

  function feedbackPayload(f) {
    return { kind: f.kind, message: f.message, posting_id: f.postingId, page_url: f.pageUrl, website: f.website };
  }

  function feedbackStatusText(resp) {
    if (resp.status === 429) return "Try again tomorrow.";
    if (resp.ok) return "Thanks, sent.";
    if (resp.status >= 500) return "Something went wrong on our side. Please try again later.";
    return "Couldn't send that. Please check the message and try again.";
  }

  $("fbSend").addEventListener("click", async function () {
    var message = $("fbMessage").value.trim();
    var problem = feedbackError(message);
    if (problem) { $("fbMsg").textContent = problem; return; }
    $("fbSend").disabled = true;
    try {
      var resp = await fetch("/api/public/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(feedbackPayload({
          kind: $("fbKind").value,
          message: message,
          postingId: state.reportId,
          pageUrl: window.location.href,
          website: $("fbWebsite").value
        }))
      });
      $("fbMsg").textContent = feedbackStatusText(resp);
      if (resp.ok) $("fbMessage").value = "";
    } catch (e) {
      $("fbMsg").textContent = "Couldn't send that. Please try again.";
    }
    $("fbSend").disabled = false;
  });

  // -------------------------------------------------------------------
  // Load
  // -------------------------------------------------------------------

  async function load() {
    try {
      var resp = await fetch("/api/public/postings");
      if (!resp.ok) throw new Error("status " + resp.status);
      var body = await resp.json();
      if (!body.enabled) { $("unavailable").hidden = false; return; }
      state.postings = body.postings || [];
      $("lastUpdated").textContent = body.last_run ? "Last updated " + fmtDateTime(body.last_run) + "." : "";
      $("board").hidden = false;
      render();
    } catch (e) {
      $("unavailable").hidden = false;
    }
  }

  load();

  // Lets the Jest unit tests reach the pure helpers. A browser has no `module`, so nothing changes there.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      fmtDate: fmtDate, fmtDateTime: fmtDateTime, centralToday: centralToday, safeUrl: safeUrl,
      titleOf: titleOf, isConfirmed: isConfirmed, matches: matches, splitByTab: splitByTab,
      feedbackError: feedbackError, feedbackPayload: feedbackPayload, feedbackStatusText: feedbackStatusText
    };
  }
})();
