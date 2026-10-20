(function () {
  "use strict";
  var API = "/api";

  var state = {
    tab: "today",
    showClosed: false,
    showOutside: false,
    query: "",
    postings: { today: [], earlier: [], archived: [] },
    sources: [],
    careersPages: [],
    companies: [],
    analytics: null,
    proposals: [],
    feedback: [],
    titlesOriginal: null,
    titlesDraft: null,
    titlesConfirmDelete: null,
    titlesFilter: "",
    budgets: null,
  };

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
    var d = new Date(+p[0], +p[1] - 1, +p[2]);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  function fmtDateTime(s) {
    if (!s) return "";
    var d = new Date(s);
    if (isNaN(d.getTime())) return String(s);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "America/Chicago" }) +
      ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZone: "America/Chicago" });
  }

  function safeUrl(u) {
    try {
      var x = new URL(String(u), window.location.href);
      return x.protocol === "https:" || x.protocol === "http:" ? x.href : null;
    } catch (e) {
      return null;
    }
  }

  function setStatus(t) { $("status").textContent = t || ""; }

  async function api(path, opts) {
    var options = Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {});
    var resp = await fetch(API + path, options);
    if (!resp.ok) {
      var detail = "";
      try {
        var body = await resp.json();
        if (typeof body.detail === "string") detail = body.detail;
        else if (body.detail && Array.isArray(body.detail.problems)) detail = body.detail.problems.join(" ");
        else detail = JSON.stringify(body.detail || "");
      } catch (e) { /* no JSON body */ }
      throw new Error(detail || "Request failed (" + resp.status + ")");
    }
    if (resp.status === 204) return null;
    return resp.json();
  }
  function apiGet(path) { return api(path); }
  function apiPost(path, body) { return api(path, { method: "POST", body: JSON.stringify(body || {}) }); }
  function apiPut(path, body) { return api(path, { method: "PUT", body: JSON.stringify(body || {}) }); }
  function apiDelete(path) { return api(path, { method: "DELETE" }); }

  $("dateLine").textContent = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  // -------------------------------------------------------------------
  // Postings
  // -------------------------------------------------------------------

  async function loadPostings() {
    setStatus("Loading…");
    try {
      var results = await Promise.all([
        apiGet("/postings?tab=today&show_closed=true"),
        apiGet("/postings?tab=earlier&show_closed=true"),
        apiGet("/postings?tab=archived"),
      ]);
      state.postings.today = results[0].postings;
      state.postings.earlier = results[1].postings;
      state.postings.archived = results[2].postings;
      setStatus("");
    } catch (e) {
      setStatus("Couldn't load postings. " + e.message);
    }
    render();
  }

  var VERIFY_LABELS = {
    feed: "Verified via the company's feed",
    structured: "Verified via the job page",
    browser: "Verified via a browser check",
    mirror: "Verified via a mirror site",
    user: "You confirmed this"
  };

  function isOpen(p) { return p.status !== "closed"; }

  function matchesQuery(p, q) {
    if (!q) return true;
    var haystack = [p.company_name, p.title, (p.locations || []).join(" "), p.source_name || ""].join(" ").toLowerCase();
    return haystack.indexOf(q) !== -1;
  }

  // The rows a list shows: filtered by the search text, closed and outside rows left out unless
  // asked for, open rows first. Also counts the closed and outside rows the filter left in play.
  function visibleRows(allRows, opts) {
    var isArchived = opts.tab === "archived";
    var rows = allRows.filter(function (p) { return matchesQuery(p, opts.query.toLowerCase()); });
    var closedHere = !isArchived ? rows.filter(function (p) { return !isOpen(p); }).length : 0;
    var outsideHere = !isArchived ? rows.filter(function (p) { return p.location_match === "outside"; }).length : 0;
    if (!isArchived && !opts.showClosed) rows = rows.filter(isOpen);
    if (!isArchived && !opts.showOutside) rows = rows.filter(function (p) { return p.location_match !== "outside"; });
    rows.sort(function (a, b) { return (isOpen(b) ? 1 : 0) - (isOpen(a) ? 1 : 0); });
    return { rows: rows, closedHere: closedHere, outsideHere: outsideHere };
  }

  function emptyStateFor(tab, q, closedHere) {
    if (q) return { title: "No postings match that filter", text: "Clear the filter to see everything in this list." };
    if (closedHere) return { title: "No open postings here", text: "Everything in this list has stopped taking applications." };
    if (tab === "today") return { title: "Nothing new yet today", text: "Tomorrow's daily search will add anything new here." };
    if (tab === "earlier") return { title: "No earlier postings", text: "Today's finds move here tomorrow and stay until you delete them." };
    return { title: "Nothing archived", text: "Postings you delete land here, and can be restored." };
  }

  function render() {
    var today = state.postings.today, earlier = state.postings.earlier, archived = state.postings.archived;
    var openToday = today.filter(isOpen);
    var openEarlier = earlier.filter(isOpen);
    var closedTotal = today.concat(earlier).filter(function (p) { return !isOpen(p); }).length;
    var appliedCount = today.concat(earlier).filter(function (p) { return p.applied; }).length;

    $("nToday").textContent = openToday.length;
    $("nEarlier").textContent = openEarlier.length;
    $("nSources").textContent = state.sources.length;
    $("nArchived").textContent = archived.length;
    $("headline").textContent = openToday.length === 1 ? "1 new posting" : openToday.length + " new postings";

    var parts = [openEarlier.length + " open from earlier searches", appliedCount + " marked applied"];
    if (closedTotal) parts.push(closedTotal + " closed and hidden");
    $("subline").textContent = parts.join(", ") + ".";

    var showList = state.tab === "today" || state.tab === "earlier" || state.tab === "archived";
    $("list").hidden = !showList;
    $("filter").hidden = !showList;
    $("sourcesView").hidden = state.tab !== "sources";
    $("analyticsView").hidden = state.tab !== "analytics";
    $("proposalsView").hidden = state.tab !== "proposals";
    $("feedbackView").hidden = state.tab !== "feedback";

    if (state.tab === "feedback") { renderFeedback(); return; }
    if (state.tab === "sources") { renderSources(); return; }
    if (state.tab === "analytics") { renderAnalytics(); return; }
    if (state.tab === "proposals") { renderProposals(); return; }
    renderPostingsList();
  }

  function renderPostingsList() {
    var list = $("list");
    list.textContent = "";
    var q = state.query.toLowerCase();
    var isArchived = state.tab === "archived";
    var visible = visibleRows(state.postings[state.tab], state);
    var rows = visible.rows;
    var closedHere = visible.closedHere;
    var outsideHere = visible.outsideHere;

    function addToggle() {
      if (closedHere) {
        var li = el("li", "toggle-row");
        var b = el("button", "btn", state.showClosed
          ? "Hide closed postings"
          : (closedHere === 1 ? "Show 1 closed posting" : "Show " + closedHere + " closed postings"));
        b.type = "button";
        b.addEventListener("click", function () { state.showClosed = !state.showClosed; render(); });
        li.appendChild(b);
        list.appendChild(li);
      }
      if (outsideHere) {
        var li2 = el("li", "toggle-row");
        var b2 = el("button", "btn", state.showOutside
          ? "Hide postings outside your locations"
          : (outsideHere === 1 ? "Show 1 posting outside your locations" : "Show " + outsideHere + " postings outside your locations"));
        b2.type = "button";
        b2.addEventListener("click", function () { state.showOutside = !state.showOutside; render(); });
        li2.appendChild(b2);
        list.appendChild(li2);
      }
    }

    if (!rows.length) {
      var li = el("li");
      var box = el("div", "empty");
      var empty = emptyStateFor(state.tab, q, closedHere);
      box.appendChild(el("strong", null, empty.title));
      box.appendChild(document.createTextNode(empty.text));
      li.appendChild(box);
      list.appendChild(li);
      addToggle();
      return;
    }

    rows.forEach(function (p) {
      var closed = !isOpen(p) && !isArchived;
      var li = el("li", "item" + (state.tab === "today" ? " new" : "") + (closed ? " closed" : ""));
      if (isArchived) li.appendChild(el("span", "badge closed", "Archived"));
      else if (closed) li.appendChild(el("span", "badge closed", "Closed"));
      else if (p.status === "closing") li.appendChild(el("span", "badge closing", "Closing soon"));
      else if (p.status === "unverified") li.appendChild(el("span", "badge unverified", "Status not confirmed"));
      li.appendChild(el("h3", "co", p.company_name || "Unknown company"));
      li.appendChild(el("div", "ti", p.title || ""));
      if (p.location_match === "unclear") li.appendChild(el("span", "tag unclear", "Location unclear"));
      else if (p.location_match === "outside") li.appendChild(el("span", "tag", "Outside your locations"));
      if (p.public_hidden) li.appendChild(el("span", "tag", "Hidden from public"));
      if (p.visitor_reports) li.appendChild(el("span", "tag unclear", "Reported by a visitor (" + p.visitor_reports + ")"));
      var meta = el("div", "meta");
      var locations = (p.locations || []).join(", ");
      if (locations) meta.appendChild(el("span", null, locations));
      if (p.source_name) meta.appendChild(el("span", null, "via " + p.source_name));
      if (p.posted_date) meta.appendChild(el("span", null, "posted " + fmtDate(p.posted_date)));
      meta.appendChild(el("span", null, "found " + fmtDate(p.first_seen)));
      if (p.verified_via && VERIFY_LABELS[p.verified_via]) {
        meta.appendChild(el("span", null, VERIFY_LABELS[p.verified_via] + (p.verified_at ? " " + fmtDate(p.verified_at) : "")));
      }
      li.appendChild(meta);
      if (p.status_note) li.appendChild(el("p", "note", p.status_note + (p.verified_at ? " Checked " + fmtDate(p.verified_at) + "." : "")));
      if (p.note && !closed) li.appendChild(el("p", "note", p.note));

      var actions = el("div", "actions");
      var href = safeUrl(p.url);
      if (href) {
        var a = el("a", closed || isArchived ? "btn" : "btn primary", closed || isArchived ? "View posting" : "Open posting");
        a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
        actions.appendChild(a);
      }

      if (isArchived) {
        var restore = el("button", "btn", "Restore");
        restore.type = "button";
        restore.addEventListener("click", function () { restorePosting(p, restore); });
        actions.appendChild(restore);
      } else {
        var ap = el("button", "btn", p.applied ? "Applied" : "Mark applied");
        ap.type = "button";
        ap.setAttribute("aria-pressed", p.applied ? "true" : "false");
        ap.addEventListener("click", function () { toggleApplied(p, ap); });
        actions.appendChild(ap);

        var markOpen = el("button", "btn", "Mark open");
        markOpen.type = "button";
        markOpen.setAttribute("aria-pressed", p.user_status === "open" ? "true" : "false");
        markOpen.addEventListener("click", function () { confirmPosting(p, "open", markOpen); });
        actions.appendChild(markOpen);

        var markClosed = el("button", "btn", "Mark closed");
        markClosed.type = "button";
        markClosed.setAttribute("aria-pressed", p.user_status === "closed" ? "true" : "false");
        markClosed.addEventListener("click", function () { confirmPosting(p, "closed", markClosed); });
        actions.appendChild(markClosed);

        var hide = el("button", "btn", "Hide from public");
        hide.type = "button";
        hide.setAttribute("aria-pressed", p.public_hidden ? "true" : "false");
        hide.addEventListener("click", function () { toggleHidden(p, hide); });
        actions.appendChild(hide);

        var del = el("button", "btn del", "Delete");
        del.type = "button";
        var timer = null;
        del.addEventListener("click", function () {
          if (!del.classList.contains("armed")) {
            del.classList.add("armed"); del.textContent = "Confirm delete";
            timer = setTimeout(function () { del.classList.remove("armed"); del.textContent = "Delete"; }, 4000);
            return;
          }
          clearTimeout(timer);
          deletePosting(p, del);
        });
        actions.appendChild(del);
      }
      li.appendChild(actions);
      list.appendChild(li);
    });
    addToggle();
  }

  async function toggleApplied(p, btn) {
    btn.disabled = true;
    try { await apiPost("/postings/" + p.id + "/applied", { applied: !p.applied }); await loadPostings(); }
    catch (e) { setStatus("Couldn't save that change. " + e.message); }
    btn.disabled = false;
  }

  async function toggleHidden(p, btn) {
    btn.disabled = true;
    var hide = !p.public_hidden;
    try {
      await apiPost("/postings/" + p.id + "/public-hidden", { hidden: hide });
      await loadPostings();
      setStatus((hide ? "Hidden from the public page." : "Shown on the public page again.") + " The public page updates within 5 minutes.");
    } catch (e) { setStatus("Couldn't save that change. " + e.message); }
    btn.disabled = false;
  }

  async function deletePosting(p, btn) {
    btn.disabled = true;
    try { await apiDelete("/postings/" + p.id); setStatus("Deleted " + p.company_name + ", " + p.title + "."); await loadPostings(); }
    catch (e) { btn.disabled = false; setStatus("Couldn't delete that posting. " + e.message); }
  }

  async function restorePosting(p, btn) {
    btn.disabled = true;
    try { await apiPost("/postings/" + p.id + "/restore"); setStatus("Restored " + p.company_name + "."); await loadPostings(); }
    catch (e) { btn.disabled = false; setStatus("Couldn't restore that posting. " + e.message); }
  }

  async function confirmPosting(p, status, btn) {
    btn.disabled = true;
    try { await apiPost("/postings/" + p.id + "/confirm", { status: status }); await loadPostings(); }
    catch (e) { setStatus("Couldn't save that change. " + e.message); }
    btn.disabled = false;
  }

  // -------------------------------------------------------------------
  // Search now
  // -------------------------------------------------------------------

  function setSearchStatus(text, isError) {
    var e = $("searchStatus");
    e.textContent = text || "";
    e.classList.toggle("err", !!isError);
  }

  var searchStartedAt = null;
  var searchElapsedTimer = null;
  var searchResultTimer = null;

  function searchingLabel() {
    if (!searchStartedAt) return "Searching…";
    var mins = Math.floor((Date.now() - searchStartedAt) / 60000);
    return mins < 1 ? "Searching…" : "Searching… " + mins + "m";
  }

  function setSearchButtonBusy(busy) {
    if (searchResultTimer) { clearTimeout(searchResultTimer); searchResultTimer = null; }
    $("searchNowBtn").disabled = busy;
    $("searchNowBtn").setAttribute("aria-busy", busy ? "true" : "false");
    $("searchNowSpinner").hidden = !busy;
    if (busy) {
      if (!searchStartedAt) searchStartedAt = Date.now();
      $("searchNowLabel").textContent = searchingLabel();
      if (!searchElapsedTimer) searchElapsedTimer = setInterval(function () { $("searchNowLabel").textContent = searchingLabel(); }, 15000);
    } else {
      searchStartedAt = null;
      if (searchElapsedTimer) { clearInterval(searchElapsedTimer); searchElapsedTimer = null; }
      $("searchNowLabel").textContent = "Search now";
    }
  }

  function briefSearchResult(result) {
    if (!result) return "Done";
    if (result.agent_outcome) {
      var issues = (result.agent_outcome.steps_skipped || []).length + (result.agent_outcome.errors || []).length;
      if (issues) return "Done, with issues";
    }
    var n = result.new_count || 0;
    return n ? "Done: " + n + " new" : "Done: nothing new";
  }

  function showBriefSearchResult(text) {
    $("searchNowLabel").textContent = text;
    if (searchResultTimer) clearTimeout(searchResultTimer);
    searchResultTimer = setTimeout(function () {
      searchResultTimer = null;
      $("searchNowLabel").textContent = "Search now";
    }, 6000);
  }

  function formatSearchResult(result) {
    if (!result) return "Done.";
    var issues = [];
    if (result.agent_outcome) {
      (result.agent_outcome.steps_skipped || []).forEach(function (s) {
        issues.push(s.step + " skipped (" + s.reason + ")");
      });
      (result.agent_outcome.errors || []).forEach(function (e) { issues.push(e); });
    }
    if (result.run_status === "degraded") {
      var failed = ((result.health || {}).failed_checks || []).map(function (c) { return c.message; });
      return "Degraded: " + (failed.length ? failed.join("; ") : "a health check failed") + ".";
    }
    if (issues.length) return "Finished, but some steps were skipped: " + issues.join("; ") + ".";
    var newCount = result.new_count || 0;
    if (!newCount) return "Done: nothing new.";
    var sourcesFound = result.sources_found || 0;
    return "Done: " + newCount + (newCount === 1 ? " new posting" : " new postings") + ", " +
      sourcesFound + (sourcesFound === 1 ? " source found" : " sources found") + ".";
  }

  function pollSearchRun(id) {
    apiGet("/search/runs/" + id).then(function (row) {
      if (row.status === "completed") {
        setSearchButtonBusy(false);
        setSearchStatus(formatSearchResult(row.result));
        showBriefSearchResult(briefSearchResult(row.result));
        loadPostings();
        return;
      }
      if (row.status === "failed") {
        setSearchButtonBusy(false);
        var err = row.result && row.result.error ? row.result.error : "unknown error";
        setSearchStatus("Run failed: " + err, true);
        showBriefSearchResult("Search failed");
        return;
      }
      setTimeout(function () { pollSearchRun(id); }, 5000);
    }).catch(function () {
      setTimeout(function () { pollSearchRun(id); }, 5000);
    });
  }

  async function postSearchStart(confirmBudget) {
    var resp = await fetch(API + "/search/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_budget: !!confirmBudget }),
    });
    var body = null;
    try { body = await resp.json(); } catch (e) { /* no JSON body */ }
    return { ok: resp.ok, status: resp.status, body: body };
  }

  function searchErrorMessage(body, fallbackStatus) {
    var detail = body && body.detail;
    if (typeof detail === "string") return detail;
    if (detail && detail.message) return detail.message;
    return "Couldn't start the search (" + fallbackStatus + ").";
  }

  async function startSearch() {
    setSearchButtonBusy(true);
    setSearchStatus("Starting…");
    try {
      var res = await postSearchStart(false);
      if (!res.ok && res.status === 409 && res.body && res.body.detail && res.body.detail.code === "budget_warning") {
        setSearchButtonBusy(false);
        setSearchStatus("");
        if (!window.confirm(res.body.detail.message)) return;
        setSearchButtonBusy(true);
        setSearchStatus("Starting…");
        res = await postSearchStart(true);
      }
      if (!res.ok) {
        setSearchButtonBusy(false);
        setSearchStatus(searchErrorMessage(res.body, res.status), true);
        return;
      }
      setSearchStatus("Searching…");
      pollSearchRun(res.body.id);
    } catch (e) {
      setSearchButtonBusy(false);
      setSearchStatus("Couldn't start the search. " + e.message, true);
    }
  }

  function checkSearchBusyOnLoad(wasBusy) {
    apiGet("/search/status").then(function (body) {
      if (body.busy) {
        setSearchButtonBusy(true);
        setSearchStatus(body.reason || "A search is already running");
        setTimeout(function () { checkSearchBusyOnLoad(true); }, 8000);
      } else if (wasBusy) {
        setSearchButtonBusy(false);
        setSearchStatus("");
        loadPostings();
      }
    }).catch(function () { /* not fatal; the button still works */ });
  }

  $("searchNowBtn").addEventListener("click", startSearch);
  checkSearchBusyOnLoad(false);

  // -------------------------------------------------------------------
  // Sources
  // -------------------------------------------------------------------

  var KIND_ORDER = ["Company career sites", "Job boards", "Trackers and aggregators", "School job boards", "Timing guides", "Added by you"];
  var KIND_HELP = {
    "Company career sites": "Official postings. The most reliable links to apply from.",
    "Job boards": "Public listings, including LinkedIn pages that show without signing in.",
    "Trackers and aggregators": "Community and automated lists of MBA and PM internships.",
    "School job boards": "Public MBA career-office boards that repost employer listings.",
    "Timing guides": "Used to predict when roles open, not as posting sources.",
    "Added by you": "Sites you added."
  };

  async function loadSources() {
    try {
      var body = await apiGet("/sources");
      state.sources = body.sources;
      state.careersPages = body.careers_pages || [];
    } catch (e) {
      setStatus("Couldn't load sources. " + e.message);
    }
    render();
  }

  var CP_STATUS_LABELS = {
    working: "Working", unchanged: "Unchanged", changed: "Changed",
    failed: "Failed", blocked: "Blocked", disallowed: "Disallowed by robots.txt"
  };
  var CP_METHOD_LABELS = { browser_run: "Browser Run", backup: "backup" };

  function renderCareersPages() {
    var panel = $("careersPagesPanel");
    var list = $("careersPagesList");
    list.textContent = "";
    panel.hidden = !state.careersPages.length;
    if (!state.careersPages.length) return;
    state.careersPages.forEach(function (c) {
      var li = el("li");
      var status = el("span", "cp-status " + c.status, CP_STATUS_LABELS[c.status] || c.status);
      li.appendChild(status);
      if (c.flagged) li.appendChild(el("span", "cp-flag", "Careers page not working"));
      var name = el("div", null);
      name.appendChild(el("b", null, c.name));
      li.appendChild(name);
      var tail = [];
      if (c.method) tail.push("via " + (CP_METHOD_LABELS[c.method] || c.method));
      if (c.last_checked) tail.push("checked " + fmtDateTime(c.last_checked));
      if (c.last_error) tail.push(c.last_error);
      if (tail.length) li.appendChild(el("div", null, tail.join(" · ")));
      list.appendChild(li);
    });
  }

  function renderSources() {
    renderCareersPages();
    var wrap = $("srcGroups");
    wrap.textContent = "";
    if (!state.sources.length) {
      var box = el("div", "empty");
      box.appendChild(el("strong", null, "No sources listed yet"));
      box.appendChild(document.createTextNode("Sources appear here after a search runs, or add one below."));
      wrap.appendChild(box);
    }
    var groups = {};
    state.sources.forEach(function (x) {
      var k = KIND_ORDER.indexOf(x.kind) === -1 ? "Job boards" : x.kind;
      (groups[k] = groups[k] || []).push(x);
    });
    KIND_ORDER.forEach(function (kind) {
      var items = groups[kind];
      if (!items || !items.length) return;
      items.sort(function (a, b) { return (b.found_count - a.found_count) || a.name.localeCompare(b.name); });
      var g = el("section", "src-group");
      g.appendChild(el("h2", null, kind));
      g.appendChild(el("p", "sub", KIND_HELP[kind] || ""));
      items.forEach(function (x) {
        var row = el("div", "src");
        var head = el("div", "src-head");
        var name = el("span", "src-name");
        var href = safeUrl(x.url);
        if (href) { var a = el("a", null, x.name); a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer"; name.appendChild(a); }
        else name.textContent = x.name;
        head.appendChild(name);
        var c = el("span", "src-count");
        var b = el("b", null, String(x.found_count));
        c.appendChild(b);
        c.appendChild(document.createTextNode(x.found_count === 1 ? " posting found" : " postings found"));
        head.appendChild(c);
        row.appendChild(head);
        if (x.covers) row.appendChild(el("p", null, x.covers));
        var tail = [];
        if (x.last_checked) tail.push("Last checked " + fmtDate(x.last_checked) + ".");
        if (x.notes) tail.push(x.notes);
        if (tail.length) row.appendChild(el("p", null, tail.join(" ")));
        var del = el("button", "btn del", "Remove");
        del.type = "button";
        var t = null;
        del.addEventListener("click", function () {
          if (!del.classList.contains("armed")) {
            del.classList.add("armed"); del.textContent = "Confirm remove";
            t = setTimeout(function () { del.classList.remove("armed"); del.textContent = "Remove"; }, 4000);
            return;
          }
          clearTimeout(t);
          removeSource(x, del);
        });
        row.appendChild(del);
        g.appendChild(row);
      });
      wrap.appendChild(g);
    });
  }

  async function removeSource(x, btn) {
    btn.disabled = true;
    try { await apiDelete("/sources/" + x.id); setStatus("Removed " + x.name + "."); await loadSources(); }
    catch (e) { btn.disabled = false; setStatus("Couldn't remove that site. " + e.message); }
  }

  $("addBtn").addEventListener("click", async function () {
    var name = $("addName").value.trim();
    var url = safeUrl($("addUrl").value.trim());
    if (!name || !url) { setStatus("Enter a site name and a full address starting with https://."); return; }
    $("addBtn").disabled = true;
    try {
      await apiPost("/sources", { name: name, url: url });
      $("addName").value = ""; $("addUrl").value = "";
      setStatus("Added " + name + ".");
      await loadSources();
    } catch (e) { setStatus("Couldn't add that site. " + e.message); }
    $("addBtn").disabled = false;
  });

  // -------------------------------------------------------------------
  // Analytics
  // -------------------------------------------------------------------

  function fmtPct(n) { return Math.round((n || 0) * 100) + "%"; }
  function fmtUsd(n) { return "$" + (n || 0).toFixed(2); }

  async function loadAnalytics() {
    try {
      state.analytics = await apiGet("/analytics");
    } catch (e) {
      setStatus("Couldn't load analytics. " + e.message);
    }
    render();
  }

  // runs.source: which path started a run (phase 1 of the Cloudflare daily trigger).
  var RUN_SOURCE_LABELS = { cloudflare: "Cloudflare", manual: "hand", schedule: "the GitHub schedule" };

  function runSourceLabel(source) {
    return RUN_SOURCE_LABELS[source] || source;
  }

  function fillList(id, items, emptyText, formatItem) {
    var ul = $(id);
    ul.textContent = "";
    if (!items.length) { ul.appendChild(el("li", null, emptyText)); return; }
    items.forEach(function (item) { ul.appendChild(el("li", null, formatItem(item))); });
  }

  function renderAnalytics() {
    var a = state.analytics;
    var hasData = a && (a.coverage.length || a.usage_by_step.length);
    $("analyticsEmpty").hidden = !!hasData;
    $("analyticsBody").hidden = !hasData;
    if (!a) return;

    var forecast = a.forecast || {};
    $("usageSummary").textContent = "Forecast this week: " + fmtUsd(forecast.weekly_usd) +
      ". Forecast this month: " + fmtUsd(forecast.monthly_usd) + ".";
    fillList("usageByStep", a.usage_by_step, "No Claude calls recorded yet.", function (u) {
      return (u.step || "") + " (" + (u.model || "") + "): " + (u.tokens || 0).toLocaleString() + " tokens, " +
        (u.searches || 0) + " searches, " + fmtUsd(u.usd);
    });

    fillList("coverageList", a.coverage, "No companies yet.", function (c) {
      return c.name + ": " + c.posting_count + (c.posting_count === 1 ? " posting" : " postings") +
        (c.last_feed_status === "no_source" ? " (no source yet)" : "");
    });

    fillList("sourceQualityList", a.sources, "No sources yet.", function (s) {
      return s.name + " (" + s.status + "): " + s.found_count + " found, " + fmtPct(s.dead_link_rate) + " dead links";
    });

    fillList("firstToFindList", a.first_to_find, "No postings found yet.", function (f) {
      return (f.source_name || "Unknown source") + ": " + f.n + (f.n === 1 ? " posting" : " postings");
    });

    fillList("perTitleList", a.postings_per_title, "No postings yet.", function (t) {
      return (t.matched_title || "Unmatched") + ": " + t.n;
    });

    $("unconfirmedSummary").textContent = a.unconfirmed_count + (a.unconfirmed_count === 1 ? " posting" : " postings") + " not yet confirmed.";
    fillList("verificationList", a.verification_methods, "Nothing verified yet.", function (v) {
      return (v.verified_via || "not yet checked") + ": " + v.n;
    });

    fillList("prunedList", a.pruned, "Nothing archived yet.", function (p) {
      return (p.archive_reason || "No reason given") + ": " + p.n;
    });

    fillList("alertHistoryList", a.alerts, "No alerts.", function (al) {
      return al.type + " (" + al.severity + "): " + al.message + " — " + fmtDate((al.created_at || "").slice(0, 10));
    });

    var perSource = a.postings_per_source || {};
    fillList("postingsPerSourceList", [
      { label: "Feeds", n: perSource.feed || 0 },
      { label: "Careers pages", n: perSource.careers_page || 0 },
      { label: "Discovery", n: perSource.discovery || 0 },
      { label: "Email alerts", n: perSource.email || 0 },
    ], "No postings yet.", function (s) { return s.label + ": " + s.n; });

    var perRun = {};
    (a.careers_pages_per_run || []).forEach(function (r) {
      var entry = perRun[r.run_id] || { run_id: r.run_id, at: r.at, browser_run: null, backup: null };
      entry[r.method] = r;
      perRun[r.run_id] = entry;
    });
    var perRunRows = Object.keys(perRun).map(function (k) { return perRun[k]; })
      .sort(function (x, y) { return y.run_id - x.run_id; }).slice(0, 10);
    fillList("careersPagesPerRunList", perRunRows, "No careers-page runs yet.", function (r) {
      var parts = [fmtDateTime(r.at)];
      if (r.browser_run) parts.push("Browser Run: " + r.browser_run.pages + " loaded (" + r.browser_run.changed + " changed, " + r.browser_run.unchanged + " unchanged, " + r.browser_run.failures + " failed)");
      if (r.backup) parts.push("backup: " + r.backup.pages + " loaded (" + r.backup.failures + " failed)");
      return parts.join(" — ");
    });

    fillList("discoverySearchesPerRunList", a.discovery_searches_per_run || [], "No discovery runs yet.", function (r) {
      return fmtDateTime(r.started_at) + ": " + (r.searches || 0) + (r.searches === 1 ? " search" : " searches");
    });

    fillList("recentRunsList", a.recent_runs || [], "No runs yet.", function (r) {
      var line = fmtDateTime(r.started_at) + ": " + (r.status === "degraded" ? "Degraded" : (r.status || "unknown")) +
        ", " + (r.new_count || 0) + " new";
      if (r.finished_at && r.started_at) {
        var mins = (new Date(r.finished_at) - new Date(r.started_at)) / 60000;
        if (mins >= 0) line += ", " + (mins < 1 ? "under 1" : Math.round(mins)) + " min" + (mins > 30 ? " (over 30)" : "");
      }
      if (r.source) line += ", started by " + runSourceLabel(r.source);
      if (r.status === "degraded" && r.summary) {
        var at = r.summary.indexOf("Run degraded");
        if (at >= 0) line += ". " + r.summary.slice(at).split(". No new postings")[0];
      }
      return line;
    });
  }

  // -------------------------------------------------------------------
  // Proposals
  // -------------------------------------------------------------------

  async function loadProposals() {
    try {
      var body = await apiGet("/proposals?status=pending");
      state.proposals = body.proposals;
    } catch (e) {
      setStatus("Couldn't load proposals. " + e.message);
    }
    render();
  }

  function proposalSummary(p) {
    var payload = p.payload || {};
    if (p.kind === "company") return "Company: " + (payload.action || "change") + " — " + (payload.name || "");
    if (p.kind === "source") return "New source: " + (payload.name || "") + (payload.url ? " (" + payload.url + ")" : "");
    if (p.kind === "source_retire") return "Retire source #" + payload.source_id;
    if (p.kind === "careers_repair") return "Replace careers page: " + (payload.company_name || "") + " — " + (payload.new_url || "");
    return p.kind;
  }

  function renderProposals() {
    var pending = state.proposals;
    $("proposalsEmpty").hidden = !!pending.length;
    var list = $("proposalsList");
    list.textContent = "";
    pending.forEach(function (p) {
      var row = el("div", "row-item");
      var name = el("div", "name");
      name.appendChild(document.createTextNode(proposalSummary(p)));
      if (p.payload && p.payload.reason) name.appendChild(el("span", null, p.payload.reason));
      row.appendChild(name);
      var approve = el("button", "btn primary", "Approve");
      approve.type = "button";
      approve.addEventListener("click", function () { decideProposal(p, "approve", approve); });
      row.appendChild(approve);
      var reject = el("button", "btn del", "Reject");
      reject.type = "button";
      reject.addEventListener("click", function () { decideProposal(p, "reject", reject); });
      row.appendChild(reject);
      list.appendChild(row);
    });
  }

  async function decideProposal(p, action, btn) {
    btn.disabled = true;
    try {
      await apiPost("/proposals/" + p.id + "/" + action);
      setStatus(action === "approve" ? "Approved." : "Rejected.");
      await loadProposals();
    } catch (e) {
      btn.disabled = false;
      setStatus("Couldn't " + action + " that proposal. " + e.message);
    }
  }

  // -------------------------------------------------------------------
  // Feedback
  // -------------------------------------------------------------------

  var FEEDBACK_KINDS = {
    bug: "Bug", dead_link: "Dead link", closed: "Job is closed",
    wrong_job: "Wrong job", suggestion: "Suggestion", other: "Other"
  };

  async function loadFeedback() {
    try {
      var body = await apiGet("/feedback");
      state.feedback = body.feedback;
    } catch (e) {
      setStatus("Couldn't load feedback. " + e.message);
    }
    $("nFeedback").textContent = state.feedback.filter(function (f) { return f.status === "new"; }).length || "";
    render();
  }

  // Jumps to a posting's card: opens the tab it is in and filters to its company.
  function showCard(postingId, company) {
    var tab = ["today", "earlier", "archived"].filter(function (name) {
      return state.postings[name].some(function (p) { return p.id === postingId; });
    })[0];
    if (!tab) { setStatus("That posting is no longer in the lists."); return; }
    state.query = (company || "").trim();
    $("filter").value = state.query;
    state.showClosed = true;
    selectTab(tab);
  }

  function renderFeedback() {
    var rows = state.feedback;
    $("feedbackEmpty").hidden = !!rows.length;
    var list = $("feedbackList");
    list.textContent = "";
    rows.forEach(function (f) {
      var row = el("div", "row-item");
      row.style.alignItems = "flex-start";
      var name = el("div", "name");
      name.appendChild(document.createTextNode((FEEDBACK_KINDS[f.kind] || f.kind) + (f.status === "new" ? " (new)" : "")));
      var message = el("span", null, f.message);  // textContent only: stored text is never read as HTML
      message.style.whiteSpace = "pre-wrap";
      message.style.color = "var(--ink)";
      name.appendChild(message);
      if (f.posting_id != null) {
        var cardLink = el("a", null, (f.posting_company || "Posting") + ", " + (f.posting_title || ""));
        cardLink.href = "#";
        cardLink.addEventListener("click", function (e) { e.preventDefault(); showCard(f.posting_id, f.posting_company); });
        var wrap = el("span", null, "Posting: ");
        wrap.appendChild(cardLink);
        name.appendChild(wrap);
      }
      name.appendChild(el("span", null, fmtDateTime(f.created_at) +
        (f.status === "read" ? ", read" : "") + (f.status === "dismissed" ? ", dismissed" : "")));
      if (f.issue_url && safeUrl(f.issue_url)) {
        var issue = el("a", null, "GitHub issue");
        issue.href = safeUrl(f.issue_url); issue.target = "_blank"; issue.rel = "noopener noreferrer";
        var issueWrap = el("span", null, "");
        issueWrap.appendChild(issue);
        name.appendChild(issueWrap);
      }
      row.appendChild(name);

      var actions = el("div", "actions");
      actions.style.marginTop = "0";
      actions.style.flexDirection = "column";
      if (f.status === "new") actions.appendChild(feedbackButton("Mark read", function (b) { return decideFeedback(f, "status", { status: "read" }, b); }));
      if (f.status !== "dismissed") actions.appendChild(feedbackButton("Dismiss", function (b) { return decideFeedback(f, "status", { status: "dismissed" }, b); }));
      if (!f.issue_url) actions.appendChild(feedbackButton("Open as GitHub issue", function (b) { return decideFeedback(f, "issue", {}, b); }));
      row.appendChild(actions);
      list.appendChild(row);
    });
  }

  function feedbackButton(label, handler) {
    var b = el("button", "btn", label);
    b.type = "button";
    b.addEventListener("click", function () { handler(b); });
    return b;
  }

  async function decideFeedback(f, action, body, btn) {
    btn.disabled = true;
    try {
      await apiPost("/feedback/" + f.id + "/" + action, body);
      if (action === "issue") setStatus("Opened a GitHub issue.");
      await Promise.all([loadFeedback(), loadPostings()]);
    } catch (e) {
      btn.disabled = false;
      setStatus("Couldn't save that. " + e.message);
    }
  }

  // -------------------------------------------------------------------
  // Budgets
  // -------------------------------------------------------------------

  // Dollars only (phase 9): the API contract is unchanged apart from the removed
  // billing_mode and token budgets. Shares travel as 0-1 decimals and show as whole percents.
  var BUDGET_FIELDS = {
    bDailyUsd: "daily_usd_budget", bMonthlyUsd: "monthly_usd_budget", bDailySearches: "daily_search_budget",
    bSearchPerRun: "search_limit_per_run", bSearchPerCompany: "search_limit_per_company",
    bBrowserChecks: "browser_checks_per_run", bFeedbackCap: "public_feedback_daily_limit",
    bShareDiscovery: "share_discovery", bShareScout: "share_scout",
    bShareClassification: "share_classification", bShareImprovement: "share_improvement",
    bModelDiscovery: "model_discovery", bModelScout: "model_scout",
    bModelClassification: "model_classification", bModelImprovement: "model_improvement",
  };
  var SHARE_BUDGET_FIELDS = ["share_discovery", "share_scout", "share_classification", "share_improvement"];
  var INTEGER_BUDGET_FIELDS = [
    "daily_search_budget", "search_limit_per_run", "search_limit_per_company", "browser_checks_per_run",
    "public_feedback_daily_limit",
  ];
  var NUMERIC_BUDGET_FIELDS = ["daily_usd_budget", "monthly_usd_budget"].concat(INTEGER_BUDGET_FIELDS, SHARE_BUDGET_FIELDS);
  var SAVE_GATE_MESSAGE = "Fix the flagged fields to save.";

  function shareToPercent(decimal) { return Math.round(Number(decimal) * 100); }
  function percentToShare(percent) { return Number(percent) / 100; }

  function fieldIdFor(field) {
    var ids = Object.keys(BUDGET_FIELDS);
    for (var i = 0; i < ids.length; i++) if (BUDGET_FIELDS[ids[i]] === field) return ids[i];
    return null;
  }

  // What the form shows for a settings doc: shares as whole percents, everything else as stored.
  function budgetsFormValues(settings) {
    var values = {};
    Object.keys(BUDGET_FIELDS).forEach(function (id) {
      var field = BUDGET_FIELDS[id];
      var value = settings[field];
      if (value === undefined || value === null) return;
      values[field] = SHARE_BUDGET_FIELDS.indexOf(field) !== -1 ? shareToPercent(value) : value;
    });
    return values;
  }

  // The PUT body's `doc`: numeric fields as numbers, percents back to 0-1 shares, the rest as typed.
  function buildBudgetsDoc(values) {
    var doc = {};
    Object.keys(values).forEach(function (field) {
      if (SHARE_BUDGET_FIELDS.indexOf(field) !== -1) doc[field] = percentToShare(values[field]);
      else if (NUMERIC_BUDGET_FIELDS.indexOf(field) !== -1) doc[field] = Number(values[field]);
      else doc[field] = values[field];
    });
    return doc;
  }

  // The problem with one numeric field's raw input, or null when it is fine.
  function budgetFieldProblem(field, raw) {
    var text = String(raw == null ? "" : raw).trim();
    if (text === "") return "Enter a number.";
    var n = Number(text);
    if (!isFinite(n)) return "Enter a number.";
    if (n < 0) return "Must be 0 or more.";
    if (SHARE_BUDGET_FIELDS.indexOf(field) !== -1 && n > 100) return "Must be 100 or less.";
    if ((SHARE_BUDGET_FIELDS.indexOf(field) !== -1 || INTEGER_BUDGET_FIELDS.indexOf(field) !== -1) && n % 1 !== 0) return "Enter a whole number.";
    return null;
  }

  // The total pill: green and complete at exactly 100%, dashed red otherwise.
  function sharesTotalState(percents) {
    var total = 0;
    percents.forEach(function (p) {
      var n = Number(String(p == null ? "" : p).trim());
      if (String(p).trim() !== "" && isFinite(n)) total += n;
    });
    total = Math.round(total * 100) / 100;
    if (total === 100) return { total: total, complete: true, text: "Total: 100% ✓" };
    var text = total < 100
      ? "Total: " + total + "% — " + (100 - total) + "% left to assign"
      : "Total: " + total + "% — " + (total - 100) + "% over";
    return { total: total, complete: false, text: text };
  }

  function fillBudgetsForm(settings) {
    var values = budgetsFormValues(settings);
    Object.keys(BUDGET_FIELDS).forEach(function (id) {
      var field = BUDGET_FIELDS[id];
      if (values[field] !== undefined) $(id).value = values[field];
    });
    validateBudgetsForm();
  }

  function readBudgetsForm() {
    var values = {};
    Object.keys(BUDGET_FIELDS).forEach(function (id) { values[BUDGET_FIELDS[id]] = $(id).value; });
    return buildBudgetsDoc(values);
  }

  function setFieldProblem(id, problem) {
    var input = $(id);
    var note = document.querySelector('.field-error[data-for="' + id + '"]');
    if (note) note.textContent = problem || "";
    if (problem) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid");
  }

  // Flags every numeric problem next to its field, updates the total pill, and gates Save.
  // Returns true when the form can be sent. Server validation stays as the backstop.
  function validateBudgetsForm() {
    var anyProblem = false;
    NUMERIC_BUDGET_FIELDS.forEach(function (field) {
      var id = fieldIdFor(field);
      var problem = budgetFieldProblem(field, $(id).value);
      setFieldProblem(id, problem);
      if (problem) anyProblem = true;
    });
    var total = sharesTotalState(SHARE_BUDGET_FIELDS.map(function (field) { return $(fieldIdFor(field)).value; }));
    var pill = $("sharesTotal");
    pill.textContent = total.text;
    pill.classList.toggle("complete", total.complete);
    pill.classList.toggle("incomplete", !total.complete);

    var ok = !anyProblem && total.complete;
    $("budgetsSave").disabled = !ok;
    var msg = $("budgetsMsg");
    if (!ok) msg.textContent = SAVE_GATE_MESSAGE;
    else if (msg.textContent === SAVE_GATE_MESSAGE) msg.textContent = "";
    return ok;
  }

  NUMERIC_BUDGET_FIELDS.forEach(function (field) {
    $(fieldIdFor(field)).addEventListener("input", validateBudgetsForm);
  });
  $("sharesEvenSplit").addEventListener("click", function () {
    SHARE_BUDGET_FIELDS.forEach(function (field) { $(fieldIdFor(field)).value = 25; });
    validateBudgetsForm();
  });

  function renderBudgetsHistory(changes) {
    var ul = $("budgetsHistory");
    ul.textContent = "";
    if (!changes.length) { ul.appendChild(el("li", null, "No changes yet.")); return; }
    changes.forEach(function (c) {
      ul.appendChild(el("li", null, fmtDate((c.changed_at || "").slice(0, 10)) + ": " + c.field + " " + c.old_value + " → " + c.new_value));
    });
  }

  function emptyUsage() { return { daily: { searches: 0, usd: 0 }, weekly: { usd: 0 }, monthly: { usd: 0 } }; }

  function renderBudgetsUsage(body) {
    var settings = body.settings || {};
    var usage = body.usage || emptyUsage();
    var resets = body.resets || {};
    $("budgetsUsage").textContent = "Today: " + fmtUsd(usage.daily.usd) + " of " + fmtUsd(settings.daily_usd_budget) +
      " · This month: " + fmtUsd(usage.monthly.usd) + " of " + fmtUsd(settings.monthly_usd_budget) +
      " · " + (usage.daily.searches || 0) + " of " + (settings.daily_search_budget || 0) + " searches used.";

    var dailyReset = resets.daily ? "Daily count reset " + fmtDateTime(resets.daily.reset_at) + "." : "Daily count never reset.";
    var weeklyReset = resets.weekly ? "Weekly count reset " + fmtDateTime(resets.weekly.reset_at) + "." : "Weekly count never reset.";
    $("budgetsLastReset").textContent = dailyReset + " " + weeklyReset;
  }

  function renderPublicSite(block, settings) {
    var site = block || {};
    var cap = site.feedback_daily_limit != null ? site.feedback_daily_limit : ((settings || {}).public_feedback_daily_limit || 0);
    $("publicSiteStatus").textContent = "Today: " + (site.feedback_today || 0) + " of " + cap + " used · " +
      (site.board_views_today || 0) + " board views";
  }

  function renderBudgetsResponse(body) {
    state.budgets = body;
    fillBudgetsForm(body.settings || {});
    var forecast = body.forecast || {};
    $("budgetsForecast").textContent = "This week: " + fmtUsd(forecast.weekly_usd) + " · This month: " + fmtUsd(forecast.monthly_usd);
    renderBudgetsUsage(body);
    renderBudgetsHistory(body.changes || []);
    renderPublicSite(body.public_site, body.settings);
    renderBrowserRun(body.browser_run);
  }

  function fmtMin(n) { return (n || 0).toFixed(1) + " min"; }
  function fmtHrs(n) { return (n || 0).toFixed(1) + " hours"; }

  function renderBrowserRun(br) {
    if (!br) return;
    $("browserRunPlan").textContent = "Plan: " + (br.plan === "paid" ? "Paid" : "Free") + ".";
    $("browserRunToday").textContent = "Today: " + fmtMin(br.today.minutes_used) + " of " +
      fmtMin(br.today.minutes_budget) + " used · " + fmtMin(br.today.minutes_left) + " left.";
    $("browserRunCloudflareFree").textContent = "Cloudflare free allowance: " + br.cloudflare_free.daily_minutes +
      " min/day (" + fmtMin(br.cloudflare_free.minutes_used) + " used), resets at " + fmtDateTime(br.cloudflare_free.reset_at) + " Central.";

    var monthEl = $("browserRunMonth");
    if (br.month) {
      monthEl.hidden = false;
      var monthText = "This month: " + fmtHrs(br.month.hours_used) + " of " + fmtHrs(br.month.hours_included) +
        " used · " + fmtHrs(br.month.hours_left) + " left.";
      if (br.month.overage_usd > 0) monthText += " Estimated overage: " + fmtUsd(br.month.overage_usd) + ".";
      monthEl.textContent = monthText;
    } else {
      monthEl.hidden = true;
    }

    var backup = br.backup_today || { pages: 0, pages_cap: 0, reasons: [] };
    var backupText = "Backup today: " + backup.pages + " of " + backup.pages_cap + " pages used";
    backupText += backup.reasons.length ? " (" + backup.reasons.join(", ") + ")." : ".";
    $("browserRunBackupToday").textContent = backupText;

    renderBrowserRunRunsTable(br.last_runs || []);
  }

  function renderBrowserRunRunsTable(runs) {
    var tbody = $("browserRunRunsBody");
    tbody.textContent = "";
    if (!runs.length) {
      var tr = el("tr");
      var td = el("td", null, "No runs recorded yet.");
      td.colSpan = 5;
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }
    runs.forEach(function (r) {
      var tr = el("tr");
      tr.appendChild(el("td", null, fmtDateTime(r.at)));
      tr.appendChild(el("td", null, String(r.browser_run_pages || 0)));
      tr.appendChild(el("td", null, fmtMin(r.browser_run_minutes)));
      tr.appendChild(el("td", null, String(r.backup_pages || 0)));
      tr.appendChild(el("td", null, String(r.failures || 0)));
      tbody.appendChild(tr);
    });
  }

  async function loadBudgets() {
    $("budgetsMsg").textContent = "";
    try {
      var body = await apiGet("/budgets");
      renderBudgetsResponse(body);
      $("locPublic").checked = !!(await apiGet("/settings/public-site")).enabled;
    } catch (e) {
      $("budgetsMsg").textContent = "Couldn't load budgets. " + e.message;
    }
  }

  $("openBudgets").addEventListener("click", function () {
    var dialog = $("budgetsDialog");
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", "");
    loadBudgets();
  });
  $("budgetsClose").addEventListener("click", function () { closeDialog($("budgetsDialog")); });
  $("budgetsSave").addEventListener("click", async function () {
    if (!validateBudgetsForm()) return; // flagged fields: nothing is sent
    $("budgetsSave").disabled = true;
    $("budgetsMsg").textContent = "Saving…";
    try {
      var body = await apiPut("/budgets", { doc: readBudgetsForm() });
      renderBudgetsResponse(body);
      await apiPut("/settings/public-site", { enabled: $("locPublic").checked });
      $("budgetsMsg").textContent = "Saved. The public page updates within 5 minutes.";
    } catch (e) {
      $("budgetsMsg").textContent = "Couldn't save. " + e.message;
    }
    $("budgetsSave").disabled = false;
  });

  async function resetBudget(scope, btn) {
    var usage = (state.budgets && state.budgets.usage) || emptyUsage();
    var settings = (state.budgets && state.budgets.settings) || {};
    var message = scope === "daily"
      ? "Today: " + fmtUsd(usage.daily.usd) + " of " + fmtUsd(settings.daily_usd_budget) +
        " and " + (usage.daily.searches || 0) + " of " + (settings.daily_search_budget || 0) +
        " searches used. Reset the count to zero? Past usage stays in Analytics."
      : "This week: " + fmtUsd(usage.weekly.usd) + " used. Reset the count to zero? Past usage stays in Analytics.";
    if (!window.confirm(message)) return;

    btn.disabled = true;
    $("budgetsMsg").textContent = "Resetting…";
    try {
      var body = await apiPost("/budgets/resets", { scope: scope });
      renderBudgetsResponse(body);
      $("budgetsMsg").textContent = (scope === "daily" ? "Today's" : "This week's") + " usage was reset.";
    } catch (e) {
      $("budgetsMsg").textContent = "Couldn't reset. " + e.message;
    }
    btn.disabled = false;
  }

  $("resetDailyBudget").addEventListener("click", function () { resetBudget("daily", $("resetDailyBudget")); });
  $("resetWeeklyBudget").addEventListener("click", function () { resetBudget("weekly", $("resetWeeklyBudget")); });

  // -------------------------------------------------------------------
  // Tabs and filter
  // -------------------------------------------------------------------

  var MORE_TABS = ["sources", "analytics", "proposals", "feedback"];

  function selectTab(t) {
    state.tab = t;
    ["today", "earlier", "archived"].forEach(function (name) {
      $("tab" + name.charAt(0).toUpperCase() + name.slice(1)).setAttribute("aria-selected", t === name ? "true" : "false");
    });
    var isMore = MORE_TABS.indexOf(t) !== -1;
    $("tabMore").setAttribute("aria-selected", isMore ? "true" : "false");
    MORE_TABS.forEach(function (name) {
      $("more" + name.charAt(0).toUpperCase() + name.slice(1)).setAttribute("aria-current", t === name ? "true" : "false");
    });
    setStatus("");
    if (t === "sources" && !state.sources.length) loadSources();
    if (t === "analytics") loadAnalytics();
    if (t === "proposals") loadProposals();
    if (t === "feedback") loadFeedback();
    render();
  }
  $("tabToday").addEventListener("click", function () { selectTab("today"); });
  $("tabEarlier").addEventListener("click", function () { selectTab("earlier"); });
  $("tabArchived").addEventListener("click", function () { selectTab("archived"); });
  $("filter").addEventListener("input", function (e) { state.query = e.target.value.trim(); render(); });

  // -------------------------------------------------------------------
  // More menu (Sources, Analytics, Proposals)
  // -------------------------------------------------------------------

  function closeMoreMenu() {
    $("moreMenu").hidden = true;
    $("tabMore").setAttribute("aria-expanded", "false");
  }

  function openMoreMenu() {
    $("moreMenu").hidden = false;
    $("tabMore").setAttribute("aria-expanded", "true");
  }

  function isMoreMenuOpen() { return !$("moreMenu").hidden; }

  $("tabMore").addEventListener("click", function () {
    if (isMoreMenuOpen()) { closeMoreMenu(); return; }
    openMoreMenu();
    var items = $("moreMenu").querySelectorAll(".more-item");
    if (items.length) items[0].focus();
  });

  $("tabMore").addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openMoreMenu();
      var items = $("moreMenu").querySelectorAll(".more-item");
      if (items.length) items[0].focus();
    }
  });

  $("moreMenu").addEventListener("keydown", function (e) {
    var items = Array.prototype.slice.call($("moreMenu").querySelectorAll(".more-item"));
    var idx = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      items[(idx + 1) % items.length].focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      items[(idx - 1 + items.length) % items.length].focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      closeMoreMenu();
      $("tabMore").focus();
    } else if (e.key === "Tab") {
      closeMoreMenu();
    }
  });

  MORE_TABS.forEach(function (name) {
    $("more" + name.charAt(0).toUpperCase() + name.slice(1)).addEventListener("click", function () {
      selectTab(name);
      closeMoreMenu();
      $("tabMore").focus();
    });
  });

  document.addEventListener("click", function (e) {
    if (!isMoreMenuOpen()) return;
    if (e.target === $("tabMore") || $("tabMore").contains(e.target) || $("moreMenu").contains(e.target)) return;
    closeMoreMenu();
  });

  // -------------------------------------------------------------------
  // Titles
  // -------------------------------------------------------------------

  var NEW_GROUP_VALUE = "__new__";

  function cloneDoc(doc) { return JSON.parse(JSON.stringify(doc)); }

  function normalizeTitleKey(s) { return String(s).toLowerCase().trim().split(/\s+/).join(" "); }

  function titlesGroupsInOrder(doc) {
    var groups = [];
    (doc ? doc.titles : []).forEach(function (t) { if (groups.indexOf(t.group) === -1) groups.push(t.group); });
    return groups;
  }

  function findDuplicateTitleOwner(doc, name) {
    var key = normalizeTitleKey(name);
    for (var i = 0; i < doc.titles.length; i++) {
      var t = doc.titles[i];
      var names = [t.title].concat(t.aliases || []);
      for (var j = 0; j < names.length; j++) {
        if (normalizeTitleKey(names[j]) === key) return t.title;
      }
    }
    return null;
  }

  function findExcludedWordIn(doc, name) {
    var key = normalizeTitleKey(name);
    var words = doc.exclude_words || [];
    for (var i = 0; i < words.length; i++) {
      if (key.indexOf(String(words[i]).toLowerCase()) !== -1) return words[i];
    }
    return null;
  }

  function countTitlesChanges(original, draft) {
    if (!original || !draft) return 0;
    var origByTitle = {}, draftByTitle = {};
    original.titles.forEach(function (t) { origByTitle[t.title] = t; });
    draft.titles.forEach(function (t) { draftByTitle[t.title] = t; });
    var count = 0;
    Object.keys(origByTitle).forEach(function (k) { if (!(k in draftByTitle)) count++; });
    Object.keys(draftByTitle).forEach(function (k) {
      if (!(k in origByTitle)) count++;
      else if (!!origByTitle[k].active !== !!draftByTitle[k].active) count++;
    });
    return count;
  }

  function draftChangeCount() { return countTitlesChanges(state.titlesOriginal, state.titlesDraft); }
  function hasUnsavedTitlesChanges() { return draftChangeCount() > 0; }

  function titleMatchesFilter(t, q) {
    if (!q) return true;
    var haystack = [t.title].concat(t.aliases || []).join(" ").toLowerCase();
    return haystack.indexOf(q) !== -1;
  }

  function titleRow(t) {
    var li = el("li", "title-row" + (t.active ? "" : " off"));
    if (state.titlesConfirmDelete === t.title) {
      li.classList.add("confirm");
      li.appendChild(el("p", "confirm-msg", "Delete '" + t.title + "'? It will be removed once you merge the pull request."));
      var row = el("div", "row");
      var delBtn = el("button", "btn del", "Delete");
      delBtn.type = "button";
      delBtn.addEventListener("click", function () { removeDraftTitle(t.title); });
      var cancelBtn = el("button", "btn", "Cancel");
      cancelBtn.type = "button";
      cancelBtn.addEventListener("click", function () { state.titlesConfirmDelete = null; renderTitlesPanel(); });
      row.appendChild(delBtn);
      row.appendChild(cancelBtn);
      li.appendChild(row);
      return li;
    }

    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!t.active;
    cb.setAttribute("aria-label", (t.active ? "Turn off " : "Turn on ") + t.title);
    cb.addEventListener("change", function () { toggleDraftTitleActive(t.title, cb.checked); });
    li.appendChild(cb);

    var info = el("div", "title-info");
    info.appendChild(el("div", "title-name", t.title));
    if (t.aliases && t.aliases.length) info.appendChild(el("span", "aliases", "Also: " + t.aliases.join(", ")));
    li.appendChild(info);

    var del = el("button", "btn title-x", "×");
    del.type = "button";
    del.setAttribute("aria-label", "Delete " + t.title);
    del.addEventListener("click", function () { state.titlesConfirmDelete = t.title; renderTitlesPanel(); });
    li.appendChild(del);

    return li;
  }

  function toggleDraftTitleActive(titleName, checked) {
    var entry = state.titlesDraft.titles.filter(function (t) { return t.title === titleName; })[0];
    if (!entry) return;
    entry.active = checked;
    renderTitlesPanel();
  }

  function removeDraftTitle(titleName) {
    state.titlesDraft.titles = state.titlesDraft.titles.filter(function (t) { return t.title !== titleName; });
    state.titlesConfirmDelete = null;
    renderTitlesPanel();
  }

  function populateTitlesGroupSelect() {
    var select = $("titlesNewGroup");
    var current = select.value;
    select.textContent = "";
    var groups = titlesGroupsInOrder(state.titlesDraft);
    groups.forEach(function (g) {
      var opt = document.createElement("option");
      opt.value = g; opt.textContent = g;
      select.appendChild(opt);
    });
    var newOpt = document.createElement("option");
    newOpt.value = NEW_GROUP_VALUE; newOpt.textContent = "New group…";
    select.appendChild(newOpt);
    if (current && (groups.indexOf(current) !== -1 || current === NEW_GROUP_VALUE)) select.value = current;
    $("titlesNewGroupName").hidden = select.value !== NEW_GROUP_VALUE;
  }

  function updateTitlesUnsavedCount() {
    var n = draftChangeCount();
    $("titlesUnsaved").textContent = n === 0 ? "No unsaved changes." : (n === 1 ? "1 unsaved change." : n + " unsaved changes.");
    $("titlesSave").disabled = n === 0;
  }

  function renderTitlesPanel() {
    if (!state.titlesDraft) return;
    var body = $("titlesBody");
    body.textContent = "";
    var groups = titlesGroupsInOrder(state.titlesDraft);
    var byGroup = {};
    state.titlesDraft.titles.forEach(function (t) { (byGroup[t.group] = byGroup[t.group] || []).push(t); });
    if (!groups.length) {
      body.appendChild(el("p", null, "No titles yet. Add one below."));
    }
    var q = state.titlesFilter || "";
    var anyVisible = false;
    groups.forEach(function (group) {
      var items = byGroup[group];
      var visible = items.filter(function (t) { return titleMatchesFilter(t, q); });
      if (!visible.length) return;
      anyVisible = true;
      var onCount = items.filter(function (t) { return t.active; }).length;
      var offCount = items.length - onCount;
      var section = el("div", "titles-group");
      var h3 = el("h3");
      h3.appendChild(el("span", null, group));
      h3.appendChild(el("span", "count", onCount + " on, " + offCount + " off"));
      section.appendChild(h3);
      var ul = el("ul", "title-list");
      visible.forEach(function (t) { ul.appendChild(titleRow(t)); });
      section.appendChild(ul);
      body.appendChild(section);
    });
    if (groups.length && q && !anyVisible) {
      body.appendChild(el("p", null, "No titles match that filter."));
    }
    populateTitlesGroupSelect();
    updateTitlesUnsavedCount();
    if (document.activeElement !== $("titlesJson")) {
      $("titlesJson").value = JSON.stringify(state.titlesDraft, null, 2);
    }
  }

  function showPendingTitlesPr(pr) {
    var p = $("titlesPending");
    if (!pr) { p.hidden = true; return; }
    p.hidden = false;
    var a = $("titlesPendingLink");
    a.href = pr.html_url || "#";
    a.textContent = "Title changes waiting for review: #" + (pr.number != null ? pr.number : "") + (pr.title ? " (" + pr.title + ")" : "");
  }

  async function loadPendingTitlesPr() {
    try {
      var body = await apiGet("/titles/pending");
      showPendingTitlesPr(body.pending);
    } catch (e) { /* not fatal; the editor still works */ }
  }

  function addDraftTitle() {
    if (!state.titlesDraft) return;
    var msgEl = $("titlesAddMsg");
    msgEl.textContent = "";
    var title = $("titlesNewTitle").value.trim();
    var groupSelect = $("titlesNewGroup").value;
    var isNewGroup = groupSelect === NEW_GROUP_VALUE;
    var group = isNewGroup ? $("titlesNewGroupName").value.trim() : groupSelect;

    if (!title) { msgEl.textContent = "Enter a title."; return; }
    if (title.length > 120) { msgEl.textContent = "Titles must be 120 characters or fewer."; return; }
    if (!group) { msgEl.textContent = "Enter a group name."; return; }
    var dup = findDuplicateTitleOwner(state.titlesDraft, title);
    if (dup) { msgEl.textContent = "'" + title + "' is already in the list, under '" + dup + "'."; return; }
    var badWord = findExcludedWordIn(state.titlesDraft, title);
    if (badWord) { msgEl.textContent = "'" + title + "' contains the excluded word '" + badWord + "'."; return; }

    state.titlesDraft.titles.push({ title: title, group: group, aliases: [], active: true });
    renderTitlesPanel();
    $("titlesNewTitle").value = "";
    if (isNewGroup) { $("titlesNewGroup").value = group; $("titlesNewGroupName").value = ""; $("titlesNewGroupName").hidden = true; }
    $("titlesNewTitle").focus();
  }

  $("titlesFilter").addEventListener("input", function (e) {
    state.titlesFilter = e.target.value.trim().toLowerCase();
    renderTitlesPanel();
  });

  $("titlesAddBtn").addEventListener("click", addDraftTitle);
  $("titlesNewTitle").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); addDraftTitle(); } });
  $("titlesNewGroupName").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); addDraftTitle(); } });
  $("titlesNewGroup").addEventListener("change", function () {
    $("titlesNewGroupName").hidden = $("titlesNewGroup").value !== NEW_GROUP_VALUE;
    if (!$("titlesNewGroupName").hidden) $("titlesNewGroupName").focus();
  });

  $("titlesJsonApply").addEventListener("click", function () {
    var doc;
    try {
      doc = JSON.parse($("titlesJson").value);
    } catch (e) {
      $("titlesMsg").textContent = "That's not valid JSON. " + e.message;
      return;
    }
    state.titlesDraft = doc;
    state.titlesConfirmDelete = null;
    renderTitlesPanel();
    $("titlesMsg").textContent = "Applied. Review below, then Save to open a pull request.";
  });

  $("openTitles").addEventListener("click", async function () {
    state.titlesDraft = null;
    state.titlesOriginal = null;
    state.titlesConfirmDelete = null;
    state.titlesFilter = "";
    $("titlesFilter").value = "";
    $("titlesBody").textContent = "Loading…";
    $("titlesAddMsg").textContent = "";
    $("titlesMsg").textContent = "";
    $("titlesUnsaved").textContent = "";
    $("titlesJsonDetails").removeAttribute("open");
    showPendingTitlesPr(null);
    var dialog = $("titlesDialog");
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", "");
    try {
      var doc = await apiGet("/titles");
      state.titlesOriginal = cloneDoc(doc);
      state.titlesDraft = cloneDoc(doc);
      renderTitlesPanel();
      loadPendingTitlesPr();
    } catch (e) {
      $("titlesBody").textContent = "Couldn't load titles. " + e.message;
    }
  });

  function closeTitlesDialogIfConfirmed() {
    if (hasUnsavedTitlesChanges() && !window.confirm("Discard " + draftChangeCount() + " unsaved title change(s)?")) return false;
    closeDialog($("titlesDialog"));
    return true;
  }
  $("titlesClose").addEventListener("click", function () { closeTitlesDialogIfConfirmed(); });
  $("titlesDialog").addEventListener("cancel", function (e) {
    if (hasUnsavedTitlesChanges() && !window.confirm("Discard " + draftChangeCount() + " unsaved title change(s)?")) e.preventDefault();
  });
  window.addEventListener("beforeunload", function (e) {
    if ($("titlesDialog").open && hasUnsavedTitlesChanges()) { e.preventDefault(); e.returnValue = ""; }
  });

  $("titlesSave").addEventListener("click", async function () {
    if (!state.titlesDraft) return;
    $("titlesSave").disabled = true;
    $("titlesMsg").textContent = "Saving…";
    try {
      var result = await apiPut("/titles", { doc: state.titlesDraft });
      state.titlesOriginal = cloneDoc(state.titlesDraft);
      showPendingTitlesPr(result.pending);
      renderTitlesPanel();
      $("titlesMsg").textContent = "Saved. Review and merge the pull request to apply it.";
    } catch (e) {
      $("titlesMsg").textContent = "Couldn't save. " + e.message;
      $("titlesSave").disabled = false;
    }
  });

  function closeDialog(d) { if (d.close) d.close(); else d.removeAttribute("open"); }

  // -------------------------------------------------------------------
  // Companies
  // -------------------------------------------------------------------

  function companyRow(c, action) {
    var row = el("div", "row-item");
    var name = el("div", "name");
    name.appendChild(document.createTextNode(c.name));
    var sub = [];
    if (c.status === "ranked" && c.rank) sub.push("Rank " + c.rank);
    if (c.careers_url) sub.push(c.feed_type ? c.feed_type : "careers link on file");
    else if (c.last_feed_status === "no_source") sub.push("no source yet");
    if (c.last_feed_status === "error" && c.last_feed_error) sub.push("feed error: " + c.last_feed_error);
    if (sub.length) name.appendChild(el("span", null, sub.join(" · ")));
    row.appendChild(name);
    action(row);
    return row;
  }

  async function loadCompanies() {
    try {
      var body = await apiGet("/companies");
      state.companies = body.companies;
    } catch (e) {
      $("companiesMsg").textContent = "Couldn't load companies. " + e.message;
      return;
    }
    renderCompanies();
  }

  function renderCompanies() {
    var review = state.companies.filter(function (c) { return c.status === "review"; });
    var active = state.companies.filter(function (c) { return c.status === "ranked" || c.status === "pinned"; })
      .sort(function (a, b) { return (a.rank || 9999) - (b.rank || 9999) || a.name.localeCompare(b.name); });
    var removed = state.companies.filter(function (c) { return c.status === "removed"; });

    $("reviewSection").hidden = !review.length;
    var reviewList = $("reviewList");
    reviewList.textContent = "";
    review.forEach(function (c) {
      reviewList.appendChild(companyRow(c, function (row) {
        var pin = el("button", "btn primary", "Pin");
        pin.type = "button";
        pin.addEventListener("click", function () { pinCompany(c); });
        var remove = el("button", "btn", "Remove");
        remove.type = "button";
        remove.addEventListener("click", function () { unpinCompany(c); });
        row.appendChild(pin);
        row.appendChild(remove);
      }));
    });

    var activeList = $("activeList");
    activeList.textContent = "";
    if (!active.length) activeList.appendChild(el("p", null, "Nothing on the list yet."));
    active.forEach(function (c) {
      activeList.appendChild(companyRow(c, function (row) {
        var status = el("span", "check-status");
        var check = el("button", "btn", "Check now");
        check.type = "button";
        check.addEventListener("click", function () { checkCompanyNow(c, check, status); });
        row.appendChild(status);
        row.appendChild(check);
        var remove = el("button", "btn del", "Remove");
        remove.type = "button";
        remove.addEventListener("click", function () { unpinCompany(c); });
        row.appendChild(remove);
      }));
    });

    $("removedSection").hidden = !removed.length;
    var removedList = $("removedList");
    removedList.textContent = "";
    removed.forEach(function (c) {
      removedList.appendChild(companyRow(c, function (row) {
        var restore = el("button", "btn", "Restore");
        restore.type = "button";
        restore.addEventListener("click", function () { pinCompany(c); });
        row.appendChild(restore);
      }));
    });
  }

  $("toggleRemoved").addEventListener("click", function () {
    var list = $("removedList");
    var hidden = !list.hidden;
    list.hidden = hidden;
    $("toggleRemoved").textContent = hidden ? "Show removed companies" : "Hide removed companies";
  });

  function pollCheckRequest(id, statusEl, attempt) {
    apiGet("/companies/check/" + id).then(function (row) {
      if (row.status === "completed") {
        var found = row.result && typeof row.result.new_count === "number" ? row.result.new_count : null;
        statusEl.textContent = found === null ? "Done." : (found === 1 ? "Done, 1 new posting." : "Done, " + found + " new postings.");
        statusEl.classList.remove("err");
        return;
      }
      if (row.status === "failed") {
        statusEl.textContent = "Check failed" + (row.result && row.result.error ? ": " + row.result.error : ".");
        statusEl.classList.add("err");
        return;
      }
      if (attempt >= 20) { statusEl.textContent = "Still running. Check back later."; return; }
      setTimeout(function () { pollCheckRequest(id, statusEl, attempt + 1); }, 5000);
    }).catch(function (e) {
      statusEl.textContent = "Couldn't get the check's status. " + e.message;
      statusEl.classList.add("err");
    });
  }

  async function checkCompanyNow(c, btn, statusEl) {
    btn.disabled = true;
    statusEl.classList.remove("err");
    statusEl.textContent = "Starting…";
    try {
      var row = await apiPost("/companies/check", { company_ids: [c.id] });
      statusEl.textContent = "Running…";
      pollCheckRequest(row.id, statusEl, 0);
    } catch (e) {
      statusEl.textContent = "Couldn't start the check. " + e.message;
      statusEl.classList.add("err");
    }
    btn.disabled = false;
  }

  async function pinCompany(c) {
    try { await apiPost("/companies/" + c.id + "/pin"); await loadCompanies(); }
    catch (e) { $("companiesMsg").textContent = "Couldn't update " + c.name + ". " + e.message; }
  }
  async function unpinCompany(c) {
    try { await apiPost("/companies/" + c.id + "/unpin"); await loadCompanies(); }
    catch (e) { $("companiesMsg").textContent = "Couldn't update " + c.name + ". " + e.message; }
  }

  $("openCompanies").addEventListener("click", function () {
    $("companiesMsg").textContent = "";
    $("pastePreviewResult").textContent = "";
    var dialog = $("companiesDialog");
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", "");
    loadCompanies();
  });
  $("companiesClose").addEventListener("click", function () { closeDialog($("companiesDialog")); });

  $("companyAddBtn").addEventListener("click", async function () {
    var name = $("companyName").value.trim();
    var url = $("companyUrl").value.trim();
    if (!name) { $("companiesMsg").textContent = "Enter a company name."; return; }
    $("companyAddBtn").disabled = true;
    try {
      await apiPost("/companies", { name: name, careers_url: url || null });
      $("companyName").value = ""; $("companyUrl").value = "";
      $("companiesMsg").textContent = "Added " + name + ".";
      await loadCompanies();
    } catch (e) {
      $("companiesMsg").textContent = "Couldn't add " + name + ". " + e.message;
    }
    $("companyAddBtn").disabled = false;
  });

  var lastPreview = [];
  $("pastePreviewBtn").addEventListener("click", async function () {
    var text = $("pasteText").value;
    if (!text.trim()) { $("companiesMsg").textContent = "Paste at least one company."; return; }
    $("pastePreviewBtn").disabled = true;
    try {
      var body = await apiPost("/companies/paste/preview", { text: text });
      lastPreview = body.entries;
      renderPastePreview();
    } catch (e) {
      $("companiesMsg").textContent = "Couldn't preview that list. " + e.message;
    }
    $("pastePreviewBtn").disabled = false;
  });

  function renderPastePreview() {
    var wrap = $("pastePreviewResult");
    wrap.textContent = "";
    if (!lastPreview.length) return;
    var table = el("table", "paste-table");
    var thead = el("thead");
    var headRow = el("tr");
    ["Save?", "Company", "Match"].forEach(function (h) { headRow.appendChild(el("th", null, h)); });
    thead.appendChild(headRow);
    table.appendChild(thead);
    var tbody = el("tbody");
    lastPreview.forEach(function (entry, i) {
      var tr = el("tr");
      var checkTd = el("td");
      if (entry.match === "new") {
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = true;
        cb.dataset.index = String(i);
        checkTd.appendChild(cb);
      }
      tr.appendChild(checkTd);
      tr.appendChild(el("td", null, entry.name || entry.raw));
      tr.appendChild(el("td", "match-" + entry.match, entry.match));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    var row = el("div", "row");
    var saveBtn = el("button", "btn primary", "Save selected");
    saveBtn.type = "button";
    saveBtn.addEventListener("click", savePastedCompanies);
    row.appendChild(saveBtn);
    wrap.appendChild(row);
  }

  async function savePastedCompanies() {
    var checked = Array.prototype.slice.call($("pastePreviewResult").querySelectorAll("input[type=checkbox]:checked"));
    var entries = checked.map(function (cb) {
      var entry = lastPreview[Number(cb.dataset.index)];
      return { name: entry.name, url: entry.url || null };
    });
    if (!entries.length) { $("companiesMsg").textContent = "Nothing selected to save."; return; }
    try {
      var body = await apiPost("/companies/paste/save", { entries: entries });
      $("companiesMsg").textContent = "Saved " + body.saved.length + (body.saved.length === 1 ? " company." : " companies.");
      $("pasteText").value = "";
      lastPreview = [];
      $("pastePreviewResult").textContent = "";
      await loadCompanies();
    } catch (e) {
      $("companiesMsg").textContent = "Couldn't save. " + e.message;
    }
  }

  // -------------------------------------------------------------------
  // Locations
  // -------------------------------------------------------------------

  $("openLocations").addEventListener("click", async function () {
    $("locationsMsg").textContent = "";
    var dialog = $("locationsDialog");
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", "");
    try {
      var loc = await apiGet("/settings/locations");
      $("locJP").checked = loc.countries.indexOf("JP") !== -1;
      $("locRemote").checked = !!loc.include_remote;
    } catch (e) {
      $("locationsMsg").textContent = "Couldn't load locations. " + e.message;
    }
  });
  $("locationsClose").addEventListener("click", function () { closeDialog($("locationsDialog")); });
  $("locationsSave").addEventListener("click", async function () {
    var countries = ["US"];
    if ($("locJP").checked) countries.push("JP");
    $("locationsSave").disabled = true;
    try {
      await apiPut("/settings/locations", { countries: countries, include_remote: $("locRemote").checked });
      $("locationsMsg").textContent = "Saved.";
    } catch (e) {
      $("locationsMsg").textContent = "Couldn't save. " + e.message;
    }
    $("locationsSave").disabled = false;
  });

  render();
  loadPostings();
  loadSources();
  loadFeedback();

  // Lets the Jest unit tests reach the pure helpers. A browser has no `module`, so nothing changes there.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      fmtDate: fmtDate, fmtDateTime: fmtDateTime, safeUrl: safeUrl, fmtPct: fmtPct, fmtUsd: fmtUsd, fmtMin: fmtMin, fmtHrs: fmtHrs,
      isOpen: isOpen, matchesQuery: matchesQuery, visibleRows: visibleRows, emptyStateFor: emptyStateFor,
      briefSearchResult: briefSearchResult, formatSearchResult: formatSearchResult, searchErrorMessage: searchErrorMessage,
      proposalSummary: proposalSummary, normalizeTitleKey: normalizeTitleKey, titlesGroupsInOrder: titlesGroupsInOrder,
      findDuplicateTitleOwner: findDuplicateTitleOwner, findExcludedWordIn: findExcludedWordIn,
      titleMatchesFilter: titleMatchesFilter, countTitlesChanges: countTitlesChanges, buildBudgetsDoc: buildBudgetsDoc,
      budgetsFormValues: budgetsFormValues, shareToPercent: shareToPercent, percentToShare: percentToShare,
      sharesTotalState: sharesTotalState, budgetFieldProblem: budgetFieldProblem
    };
  }
})();
