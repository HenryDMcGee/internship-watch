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
    companies: [],
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
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || "");
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
    $("placeholderView").hidden = state.tab !== "analytics" && state.tab !== "proposals";

    if (state.tab === "sources") { renderSources(); return; }
    if (state.tab === "analytics" || state.tab === "proposals") { renderPlaceholder(); return; }
    renderPostingsList();
  }

  function renderPlaceholder() {
    $("placeholderTitle").textContent = state.tab === "analytics" ? "Analytics is coming later" : "Proposals is coming later";
    $("placeholderText").textContent = state.tab === "analytics"
      ? "Coverage, source quality, and usage against budget arrive with the Claude agent phase."
      : "Company and source changes that need your approval will show up here.";
  }

  function renderPostingsList() {
    var list = $("list");
    list.textContent = "";
    var q = state.query.toLowerCase();
    var isArchived = state.tab === "archived";
    var rows = state.postings[state.tab].filter(function (p) { return matchesQuery(p, q); });
    var closedHere = !isArchived ? rows.filter(function (p) { return !isOpen(p); }).length : 0;
    var outsideHere = !isArchived ? rows.filter(function (p) { return p.location_match === "outside"; }).length : 0;
    if (!isArchived && !state.showClosed) rows = rows.filter(isOpen);
    if (!isArchived && !state.showOutside) rows = rows.filter(function (p) { return p.location_match !== "outside"; });
    rows.sort(function (a, b) { return (isOpen(b) ? 1 : 0) - (isOpen(a) ? 1 : 0); });

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
      if (q) {
        box.appendChild(el("strong", null, "No postings match that filter"));
        box.appendChild(document.createTextNode("Clear the filter to see everything in this list."));
      } else if (closedHere) {
        box.appendChild(el("strong", null, "No open postings here"));
        box.appendChild(document.createTextNode("Everything in this list has stopped taking applications."));
      } else if (state.tab === "today") {
        box.appendChild(el("strong", null, "Nothing new yet today"));
        box.appendChild(document.createTextNode("Tomorrow's daily search will add anything new here."));
      } else if (state.tab === "earlier") {
        box.appendChild(el("strong", null, "No earlier postings"));
        box.appendChild(document.createTextNode("Today's finds move here tomorrow and stay until you delete them."));
      } else {
        box.appendChild(el("strong", null, "Nothing archived"));
        box.appendChild(document.createTextNode("Postings you delete land here, and can be restored."));
      }
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
    } catch (e) {
      setStatus("Couldn't load sources. " + e.message);
    }
    render();
  }

  function renderSources() {
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
  // Tabs and filter
  // -------------------------------------------------------------------

  function selectTab(t) {
    state.tab = t;
    ["today", "earlier", "sources", "archived", "analytics", "proposals"].forEach(function (name) {
      $("tab" + name.charAt(0).toUpperCase() + name.slice(1)).setAttribute("aria-selected", t === name ? "true" : "false");
    });
    setStatus("");
    if (t === "sources" && !state.sources.length) loadSources();
    render();
  }
  $("tabToday").addEventListener("click", function () { selectTab("today"); });
  $("tabEarlier").addEventListener("click", function () { selectTab("earlier"); });
  $("tabSources").addEventListener("click", function () { selectTab("sources"); });
  $("tabArchived").addEventListener("click", function () { selectTab("archived"); });
  $("tabAnalytics").addEventListener("click", function () { selectTab("analytics"); });
  $("tabProposals").addEventListener("click", function () { selectTab("proposals"); });
  $("filter").addEventListener("input", function (e) { state.query = e.target.value.trim(); render(); });

  // -------------------------------------------------------------------
  // Titles
  // -------------------------------------------------------------------

  function renderTitlesGroups(doc) {
    var body = $("titlesBody");
    body.textContent = "";
    var groups = {};
    var order = [];
    doc.titles.forEach(function (t) {
      if (!groups[t.group]) { groups[t.group] = []; order.push(t.group); }
      groups[t.group].push(t);
    });
    order.forEach(function (group) {
      var section = el("div", "titles-group");
      section.appendChild(el("h3", null, group));
      var ul = el("ul");
      groups[group].forEach(function (t) {
        var li = el("li");
        li.appendChild(document.createTextNode(t.title + (t.active ? "" : " (inactive)")));
        if (t.aliases && t.aliases.length) li.appendChild(el("span", "aliases", " — also: " + t.aliases.join(", ")));
        ul.appendChild(li);
      });
      section.appendChild(ul);
      body.appendChild(section);
    });
  }

  function showPendingTitlesPr(pr) {
    var p = $("titlesPending");
    if (!pr) { p.hidden = true; return; }
    p.hidden = false;
    var a = $("titlesPendingLink");
    a.href = pr.html_url || "#";
    a.textContent = "Pending change: " + (pr.title || "see the open pull request");
  }

  async function loadPendingTitlesPr() {
    try {
      var body = await apiGet("/titles/pending");
      showPendingTitlesPr(body.pending);
    } catch (e) { /* not fatal; the editor still works */ }
  }

  $("openTitles").addEventListener("click", async function () {
    var body = $("titlesBody");
    body.textContent = "Loading…";
    $("titlesMsg").textContent = "";
    showPendingTitlesPr(null);
    var dialog = $("titlesDialog");
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", "");
    try {
      var doc = await apiGet("/titles");
      renderTitlesGroups(doc);
      $("titlesJson").value = JSON.stringify(doc, null, 2);
      loadPendingTitlesPr();
    } catch (e) {
      body.textContent = "Couldn't load titles. " + e.message;
    }
  });
  $("titlesClose").addEventListener("click", function () { closeDialog($("titlesDialog")); });
  $("titlesSave").addEventListener("click", async function () {
    var doc;
    try {
      doc = JSON.parse($("titlesJson").value);
    } catch (e) {
      $("titlesMsg").textContent = "That's not valid JSON. " + e.message;
      return;
    }
    $("titlesSave").disabled = true;
    $("titlesMsg").textContent = "Saving…";
    try {
      var result = await apiPut("/titles", { doc: doc });
      renderTitlesGroups(doc);
      showPendingTitlesPr(result.pending);
      $("titlesMsg").textContent = "Saved. Review and merge the pull request to apply it.";
    } catch (e) {
      $("titlesMsg").textContent = "Couldn't save. " + e.message;
    }
    $("titlesSave").disabled = false;
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
})();
