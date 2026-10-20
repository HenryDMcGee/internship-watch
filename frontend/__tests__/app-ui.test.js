const { jsonResponse, posting, freshData, bootApp, flush, $, click, type, buttonByText } = require("./app-fixtures");

const text = (id) => $(id).textContent;
const cards = () => Array.from(document.querySelectorAll("#list li.item"));

describe("postings", () => {
  test("headline, counts and the three tabs", async () => {
    const data = freshData();
    data.today.push(posting({ id: 3, status: "closed", title: "Old", applied: 1 }));
    await bootApp(data);
    expect(text("headline")).toBe("1 new posting");
    expect(text("subline")).toBe("1 open from earlier searches, 1 marked applied, 1 closed and hidden.");
    expect(text("nToday")).toBe("1");
    expect(cards()).toHaveLength(1);

    click("tabEarlier");
    expect(cards().map((c) => c.querySelector("h3").textContent)).toEqual(["Google"]);
    click("tabArchived");
    expect(document.querySelector("#list .empty strong").textContent).toBe("Nothing archived");
    click("tabToday");
    expect($("tabToday").getAttribute("aria-selected")).toBe("true");
  });

  test("the headline is singular and plural", async () => {
    const data = freshData();
    data.today = [];
    await bootApp(data);
    expect(text("headline")).toBe("0 new postings");
    expect(document.querySelector("#list .empty strong").textContent).toBe("Nothing new yet today");
  });

  test("a failed load shows a message", async () => {
    const { data } = await bootApp(freshData(), { "GET /api/postings": () => jsonResponse({ detail: "boom" }, 500) });
    expect(text("status")).toMatch(/Couldn't load postings\. boom/);
    expect(data).toBeTruthy();
  });

  test("search filters cards and a no-match message shows", async () => {
    const data = freshData();
    data.today.push(posting({ id: 5, company_name: "Meta", title: "Product Intern", locations: [] }));
    await bootApp(data);
    expect(cards()).toHaveLength(2);
    type("filter", "meta");
    expect(cards()).toHaveLength(1);
    type("filter", "zzz");
    expect(document.querySelector("#list .empty strong").textContent).toBe("No postings match that filter");
  });

  test("card details: badges, tags, notes and verification", async () => {
    const data = freshData();
    data.today = [
      posting({ id: 1, status: "closing", location_match: "unclear", public_hidden: 1, visitor_reports: 2, note: "my note", status_note: "Ends soon", user_status: "open", applied: 1 }),
      posting({ id: 2, status: "unverified", company_name: "", url: "javascript:x", verified_via: "mystery", posted_date: null, source_name: null, locations: [] }),
      posting({ id: 3, status: "closed", note: "hidden note", location_match: "outside", verified_at: null }),
    ];
    await bootApp(data);
    click("tabToday");
    const first = cards()[0];
    expect(first.textContent).toContain("Closing soon");
    expect(first.textContent).toContain("Location unclear");
    expect(first.textContent).toContain("Hidden from public");
    expect(first.textContent).toContain("Reported by a visitor (2)");
    expect(first.textContent).toContain("my note");
    expect(first.textContent).toContain("Ends soon Checked");
    expect(first.querySelector("[aria-pressed='true']")).not.toBeNull();
    const second = cards()[1];
    expect(second.textContent).toContain("Status not confirmed");
    expect(second.textContent).toContain("Unknown company");
    expect(second.querySelector("a")).toBeNull();
    // closed rows start hidden behind a toggle, as do rows outside the user's locations
    expect(cards()).toHaveLength(2);
    expect(buttonByText($("list"), "Show 1 closed posting")).toBeTruthy();
    expect(buttonByText($("list"), "Show 1 posting outside your locations")).toBeTruthy();
    buttonByText($("list"), "Show 1 closed posting").click();
    expect(buttonByText($("list"), "Hide closed postings")).toBeTruthy();
    buttonByText($("list"), "Show 1 posting outside your locations").click();
    expect(cards()).toHaveLength(3);
    const closed = cards()[2];
    expect(closed.className).toContain("closed");
    expect(closed.textContent).not.toContain("hidden note");
    expect(closed.textContent).toContain("View posting");
    buttonByText($("list"), "Hide postings outside your locations").click();
    expect(cards()).toHaveLength(2);
  });

  test("toggle labels are plural for several rows", async () => {
    const data = freshData();
    data.today = [
      posting({ id: 1, status: "closed" }), posting({ id: 2, status: "closed" }),
      posting({ id: 3, location_match: "outside" }), posting({ id: 4, location_match: "outside" }),
      posting({ id: 5 }),
    ];
    await bootApp(data);
    expect(buttonByText($("list"), "Show 2 closed postings")).toBeTruthy();
    expect(buttonByText($("list"), "Show 2 postings outside your locations")).toBeTruthy();
  });

  test("only closed rows leave a clear empty state", async () => {
    const data = freshData();
    data.today = [posting({ id: 1, status: "closed" })];
    await bootApp(data);
    expect(document.querySelector("#list .empty strong").textContent).toBe("No open postings here");
    expect(buttonByText($("list"), "Show 1 closed posting")).toBeTruthy();
  });

  test("Mark applied persists through a reload", async () => {
    const data = freshData();
    const extra = {
      "POST /api/postings/1/applied": (u, o) => {
        data.today[0].applied = JSON.parse(o.body).applied ? 1 : 0;
        return jsonResponse({});
      },
    };
    const { fetchMock } = await bootApp(data, extra);
    buttonByText(cards()[0], "Mark applied").click();
    await flush();
    expect(fetchMock.calls.find((c) => c.url.endsWith("/applied")).body).toEqual({ applied: true });
    expect(buttonByText(cards()[0], "Applied")).toBeTruthy();
    expect(text("subline")).toContain("1 marked applied");
  });

  test("Mark open, Mark closed, Hide from public", async () => {
    const data = freshData();
    const extra = {
      "POST /api/postings/1/confirm": () => jsonResponse({}),
      "POST /api/postings/1/public-hidden": (u, o) => {
        data.today[0].public_hidden = JSON.parse(o.body).hidden ? 1 : 0;
        return jsonResponse({});
      },
    };
    const { fetchMock } = await bootApp(data, extra);
    buttonByText(cards()[0], "Mark open").click();
    await flush();
    buttonByText(cards()[0], "Mark closed").click();
    await flush();
    const confirms = fetchMock.calls.filter((c) => c.url.endsWith("/confirm")).map((c) => c.body.status);
    expect(confirms).toEqual(["open", "closed"]);
    buttonByText(cards()[0], "Hide from public").click();
    await flush();
    expect(cards()[0].textContent).toContain("Hidden from public");
    expect(text("status")).toBe("Hidden from the public page. The public page updates within 5 minutes.");
    buttonByText(cards()[0], "Hide from public").click();
    await flush();
    expect(text("status")).toMatch(/Shown on the public page again/);
  });

  test("a failed action shows the error and re-enables the button", async () => {
    const data = freshData();
    const fail = () => jsonResponse({ detail: "nope" }, 400);
    await bootApp(data, {
      "POST /api/postings/1/applied": fail, "POST /api/postings/1/confirm": fail,
      "POST /api/postings/1/public-hidden": fail, "DELETE /api/postings/1": fail,
    });
    for (const label of ["Mark applied", "Mark open", "Hide from public"]) {
      buttonByText(cards()[0], label).click();
      await flush();
      expect(text("status")).toBe("Couldn't save that change. nope");
      expect(buttonByText(cards()[0], label).disabled).toBe(false);
    }
    const del = buttonByText(cards()[0], "Delete");
    del.click();
    del.click();
    await flush();
    expect(text("status")).toBe("Couldn't delete that posting. nope");
  });

  test("Delete asks twice, disarms after a pause, then archives and restores", async () => {
    jest.useFakeTimers();
    try {
      const data = freshData();
      await bootApp(data, {
        "DELETE /api/postings/1": () => {
          data.archived = data.today.splice(0, 1);
          return { ok: true, status: 204, json: async () => null };
        },
        "POST /api/postings/1/restore": () => {
          data.today = data.archived.splice(0, 1);
          return jsonResponse({});
        },
      });
      await jest.advanceTimersByTimeAsync(10);
      let del = buttonByText(cards()[0], "Delete");
      del.click();
      expect(del.textContent).toBe("Confirm delete");
      jest.advanceTimersByTime(4100);
      expect(del.textContent).toBe("Delete");
      del.click();
      del.click();
      await jest.advanceTimersByTimeAsync(10);
      expect(text("status")).toBe("");
      click("tabArchived");
      expect(cards()).toHaveLength(1);
      expect(cards()[0].textContent).toContain("Archived");
      buttonByText(cards()[0], "Restore").click();
      await jest.advanceTimersByTimeAsync(10);
      click("tabToday");
      expect(cards()).toHaveLength(1);
    } finally {
      jest.useRealTimers();
    }
  });

  test("Restore failing reports it", async () => {
    const data = freshData();
    data.archived = [posting({ id: 9 })];
    await bootApp(data, { "POST /api/postings/9/restore": () => jsonResponse({ detail: { problems: ["a", "b"] } }, 400) });
    click("tabArchived");
    buttonByText(cards()[0], "Restore").click();
    await flush();
    expect(text("status")).toBe("Couldn't restore that posting. a b");
  });

  test("error bodies fall back to the status code", async () => {
    const data = freshData();
    await bootApp(data, {
      "POST /api/postings/1/applied": () => ({ ok: false, status: 502, json: async () => { throw new Error("html"); } }),
    });
    buttonByText(cards()[0], "Mark applied").click();
    await flush();
    expect(text("status")).toBe("Couldn't save that change. Request failed (502)");
  });

  test("an error with an object detail is stringified", async () => {
    await bootApp(freshData(), {
      "POST /api/postings/1/applied": () => jsonResponse({ detail: { code: 7 } }, 400),
    });
    buttonByText(cards()[0], "Mark applied").click();
    await flush();
    expect(text("status")).toContain('{"code":7}');
  });
});

describe("Search now", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  const tick = (ms) => jest.advanceTimersByTimeAsync(ms);

  test("starts, polls, and reports a finished run", async () => {
    const data = freshData();
    let polls = 0;
    await bootApp(data, {
      "POST /api/search/runs": () => jsonResponse({ id: 4 }),
      "GET /api/search/runs/4": () => {
        polls += 1;
        if (polls === 1) throw new Error("blip");
        if (polls === 2) return jsonResponse({ status: "running" });
        return jsonResponse({ status: "completed", result: { new_count: 2, sources_found: 1 } });
      },
    });
    await tick(10);
    click("searchNowBtn");
    await tick(10);
    expect($("searchNowBtn").disabled).toBe(true);
    expect(text("searchStatus")).toBe("Searching…");
    await tick(5000);
    await tick(5000);
    await tick(10);
    expect(text("searchStatus")).toBe("Done: 2 new postings, 1 source found.");
    expect(text("searchNowLabel")).toBe("Done: 2 new");
    await tick(6100);
    expect(text("searchNowLabel")).toBe("Search now");
  });

  test("elapsed minutes show on a long search", async () => {
    await bootApp(freshData(), {
      "POST /api/search/runs": () => jsonResponse({ id: 4 }),
      "GET /api/search/runs/4": () => jsonResponse({ status: "running" }),
    });
    await tick(10);
    click("searchNowBtn");
    await tick(10);
    await tick(61000);
    expect(text("searchNowLabel")).toMatch(/^Searching… \dm$/);
  });

  test("a failed run says so", async () => {
    await bootApp(freshData(), {
      "POST /api/search/runs": () => jsonResponse({ id: 4 }),
      "GET /api/search/runs/4": () => jsonResponse({ status: "failed", result: { error: "kaput" } }),
    });
    await tick(10);
    click("searchNowBtn");
    await tick(100);
    expect(text("searchStatus")).toBe("Run failed: kaput");
    expect(text("searchNowLabel")).toBe("Search failed");
    expect($("searchStatus").classList.contains("err")).toBe(true);
  });

  test("a failed run without a reason says unknown error", async () => {
    await bootApp(freshData(), {
      "POST /api/search/runs": () => jsonResponse({ id: 4 }),
      "GET /api/search/runs/4": () => jsonResponse({ status: "failed" }),
    });
    await tick(10);
    click("searchNowBtn");
    await tick(100);
    expect(text("searchStatus")).toBe("Run failed: unknown error");
  });

  test("a budget warning asks first and can be declined or accepted", async () => {
    const warning = () => jsonResponse({ detail: { code: "budget_warning", message: "Over budget" } }, 409);
    let confirmed = false;
    const { fetchMock } = await bootApp(freshData(), {
      "POST /api/search/runs": (u, o) => {
        const body = JSON.parse(o.body);
        return body.confirm_budget ? (confirmed = true, jsonResponse({ id: 4 })) : warning();
      },
      "GET /api/search/runs/4": () => jsonResponse({ status: "completed", result: null }),
    });
    await tick(10);
    window.confirm = jest.fn(() => false);
    click("searchNowBtn");
    await tick(10);
    expect(window.confirm).toHaveBeenCalledWith("Over budget");
    expect(confirmed).toBe(false);
    expect($("searchNowBtn").disabled).toBe(false);

    window.confirm = jest.fn(() => true);
    click("searchNowBtn");
    await tick(100);
    expect(confirmed).toBe(true);
    expect(text("searchStatus")).toBe("Done.");
    expect(text("searchNowLabel")).toBe("Done");
    expect(fetchMock.calls.filter((c) => c.url === "/api/search/runs")).toHaveLength(3);
  });

  test("a refused start shows the server's message, a network error shows the reason", async () => {
    let mode = "refuse";
    await bootApp(freshData(), {
      "POST /api/search/runs": () => {
        if (mode === "net") throw new Error("offline");
        return { ok: false, status: 500, json: async () => { throw new Error("no body"); } };
      },
    });
    await tick(10);
    click("searchNowBtn");
    await tick(10);
    expect(text("searchStatus")).toBe("Couldn't start the search (500).");
    mode = "net";
    click("searchNowBtn");
    await tick(10);
    expect(text("searchStatus")).toBe("Couldn't start the search. offline");
  });

  test("a search already running on load disables the button until it ends", async () => {
    const data = freshData();
    data.searchStatus = { busy: true, reason: "Daily search is running" };
    await bootApp(data);
    await tick(10);
    expect($("searchNowBtn").disabled).toBe(true);
    expect(text("searchStatus")).toBe("Daily search is running");
    data.searchStatus = { busy: false };
    await tick(8100);
    expect($("searchNowBtn").disabled).toBe(false);
    expect(text("searchStatus")).toBe("");
  });

  test("a busy search with no reason uses the default text", async () => {
    const data = freshData();
    data.searchStatus = { busy: true };
    await bootApp(data);
    await tick(10);
    expect(text("searchStatus")).toBe("A search is already running");
  });

  test("a status check that fails is ignored", async () => {
    await bootApp(freshData(), { "GET /api/search/status": () => { throw new Error("x"); } });
    await tick(10);
    expect($("searchNowBtn").disabled).toBe(false);
  });
});

describe("Sources", () => {
  test("lists groups, careers pages, and removes a source", async () => {
    const data = freshData();
    data.sources = [
      { id: 1, name: "B", url: "https://b.com", kind: "Company career sites", found_count: 1, covers: "", last_checked: null, notes: "" },
      { id: 2, name: "A", url: "javascript:x", kind: "Company career sites", found_count: 1 },
      { id: 3, name: "C", url: "https://c.com", kind: "Weird kind", found_count: 5, covers: "c", last_checked: "2026-10-01", notes: "n" },
    ];
    data.careersPages = [
      { name: "Acme", status: "failed", flagged: true, method: "browser_run", last_checked: "2026-10-14T13:00:00Z", last_error: "timeout" },
      { name: "Beta", status: "mystery", method: "other" },
    ];
    await bootApp(data, { "DELETE /api/sources/2": () => ({ ok: true, status: 204, json: async () => null }) });
    click("tabMore");
    click("moreSources");
    await flush();
    expect($("sourcesView").hidden).toBe(false);
    expect(document.querySelectorAll("#srcGroups .src-group")).toHaveLength(2);
    expect($("careersPagesPanel").hidden).toBe(false);
    expect($("careersPagesList").textContent).toContain("Careers page not working");
    expect($("careersPagesList").textContent).toContain("via Browser Run");
    expect($("careersPagesList").textContent).toContain("Beta");

    const remove = buttonByText(document.querySelector("#srcGroups"), "Remove");
    remove.click();
    expect(remove.textContent).toBe("Confirm remove");
    remove.click();
    await flush();
    expect(text("status")).toBe("Removed A.");
  });

  test("the empty state and a failed remove", async () => {
    const data = freshData();
    data.sources = [];
    await bootApp(data);
    click("tabMore");
    click("moreSources");
    await flush();
    expect($("srcGroups").textContent).toContain("No sources listed yet");
    data.sources = [{ id: 1, name: "A", url: "https://a.com", kind: "Job boards", found_count: 1 }];
    click("tabArchived");
    click("tabMore");
    click("moreSources");
    await flush();
  });

  test("a failed remove reports it; a failed load reports it", async () => {
    await bootApp(freshData(), { "DELETE /api/sources/1": () => jsonResponse({ detail: "no" }, 400) });
    click("tabMore");
    click("moreSources");
    await flush();
    const remove = buttonByText($("srcGroups"), "Remove");
    remove.click();
    remove.click();
    await flush();
    expect(text("status")).toBe("Couldn't remove that site. no");
  });

  test("a sources load error", async () => {
    await bootApp(freshData(), { "GET /api/sources": () => jsonResponse({ detail: "down" }, 500) });
    click("tabMore");
    click("moreSources");
    await flush();
    expect(text("status")).toBe("Couldn't load sources. down");
  });

  test("Add a site validates, posts and reloads", async () => {
    const data = freshData();
    const { fetchMock } = await bootApp(data, { "POST /api/sources": () => jsonResponse({}) });
    click("addBtn");
    expect(text("status")).toMatch(/Enter a site name/);
    type("addName", "Site");
    type("addUrl", "mailto:a@b.com");
    click("addBtn");
    expect(text("status")).toMatch(/Enter a site name/);
    type("addUrl", "https://site.com");
    click("addBtn");
    await flush();
    expect(fetchMock.calls.find((c) => c.method === "POST" && c.url === "/api/sources").body).toEqual({ name: "Site", url: "https://site.com/" });
    expect(text("status")).toBe("Added Site.");
    expect($("addName").value).toBe("");
  });

  test("Add a site reports an error", async () => {
    await bootApp(freshData(), { "POST /api/sources": () => jsonResponse({ detail: "dup" }, 409) });
    type("addName", "Site");
    type("addUrl", "https://site.com");
    click("addBtn");
    await flush();
    expect(text("status")).toBe("Couldn't add that site. dup");
  });
});

describe("Analytics", () => {
  const analytics = () => ({
    budgets: { daily_usd_budget: 2 },
    forecast: { weekly_usd: 1.5, monthly_usd: 2 },
    usage_by_step: [{ step: "discovery", model: "sonnet", tokens: 10, searches: 1, usd: 0.1 }],
    coverage: [{ name: "Amazon", posting_count: 1, last_feed_status: "ok" }, { name: "Acme", posting_count: 2, last_feed_status: "no_source" }],
    sources: [{ name: "S", status: "ok", found_count: 1, dead_link_rate: 0.1 }],
    first_to_find: [{ source_name: "S", n: 1 }, { source_name: null, n: 2 }],
    postings_per_title: [{ matched_title: "PM", n: 1 }, { matched_title: null, n: 1 }],
    unconfirmed_count: 1,
    verification_methods: [{ verified_via: "feed", n: 1 }, { verified_via: null, n: 1 }],
    pruned: [{ archive_reason: "stale", n: 1 }, { archive_reason: null, n: 1 }],
    alerts: [{ type: "t", severity: "high", message: "m", created_at: "2026-10-14T00:00:00Z" }, { type: "u", severity: "low", message: "n" }],
    postings_per_source: { feed: 1 },
    careers_pages_per_run: [
      { run_id: 1, at: "2026-10-14T13:00:00Z", method: "browser_run", pages: 3, changed: 1, unchanged: 1, failures: 1 },
      { run_id: 1, at: "2026-10-14T13:00:00Z", method: "backup", pages: 1, failures: 0 },
      { run_id: 2, at: "2026-10-13T13:00:00Z", method: "backup", pages: 1, failures: 0 },
    ],
    discovery_searches_per_run: [{ started_at: "2026-10-14T13:00:00Z", searches: 1 }, { started_at: "2026-10-13T13:00:00Z", searches: 2 }],
    recent_runs: [
      { started_at: "2026-10-14T13:00:00Z", finished_at: "2026-10-14T13:00:30Z", status: "completed", new_count: 1, source: "cloudflare" },
      { started_at: "2026-10-13T13:00:00Z", finished_at: "2026-10-13T14:00:00Z", status: "degraded", summary: "x. Run degraded because y. No new postings today", new_count: 0, source: "manual" },
      { started_at: "2026-10-12T13:00:00Z", finished_at: "2026-10-12T13:10:00Z", status: "degraded", summary: "plain", source: "schedule" },
      { started_at: "2026-10-11T13:00:00Z", status: null, source: "github-fallback" },
      { started_at: "2026-10-10T13:00:00Z", finished_at: "2026-10-10T13:20:00Z", status: "completed", new_count: 0 },
    ],
  });

  test("shows every section", async () => {
    const data = freshData();
    data.analytics = analytics();
    await bootApp(data);
    click("tabMore");
    click("moreAnalytics");
    await flush();
    expect($("analyticsBody").hidden).toBe(false);
    expect(text("usageSummary")).toBe("Forecast this week: $1.50. Forecast this month: $2.00.");
    expect(text("usageSummary")).not.toMatch(/[Bb]illing|token/);
    expect(text("usageByStep")).toContain("discovery (sonnet)");
    expect(text("coverageList")).toContain("(no source yet)");
    expect(text("sourceQualityList")).toContain("10% dead links");
    expect(text("firstToFindList")).toContain("Unknown source: 2 postings");
    expect(text("perTitleList")).toContain("Unmatched: 1");
    expect(text("unconfirmedSummary")).toBe("1 posting not yet confirmed.");
    expect(text("verificationList")).toContain("not yet checked: 1");
    expect(text("prunedList")).toContain("No reason given: 1");
    expect(text("alertHistoryList")).toContain("t (high): m");
    expect(text("postingsPerSourceList")).toContain("Feeds: 1");
    expect(text("careersPagesPerRunList")).toContain("Browser Run: 3 loaded");
    expect(text("discoverySearchesPerRunList")).toContain("1 search");
    expect(text("recentRunsList")).toContain("Degraded");
    expect(text("recentRunsList")).toContain("(over 30)");
    expect(text("recentRunsList")).toContain("Run degraded because y");
    expect(text("recentRunsList")).toContain("under 1 min");
  });

  test("each recent run says which path started it", async () => {
    const data = freshData();
    data.analytics = analytics();
    await bootApp(data);
    click("tabMore");
    click("moreAnalytics");
    await flush();
    const lines = Array.from($("recentRunsList").querySelectorAll("li")).map(function (li) { return li.textContent; });
    expect(lines[0]).toContain("1 new, under 1 min, started by Cloudflare");
    expect(lines[1]).toContain("0 new, 60 min (over 30), started by hand. Run degraded because y");
    expect(lines[2]).toContain("started by the GitHub schedule");
    expect(lines[3]).toContain("started by github-fallback");
    expect(lines[4]).not.toContain("started by");
  });

  test("empty analytics show the empty note, and sparse data uses fallbacks", async () => {
    const data = freshData();
    data.analytics = { budgets: null, forecast: {}, usage_by_step: [], coverage: [], sources: [], first_to_find: [], postings_per_title: [], unconfirmed_count: 0, verification_methods: [], pruned: [], alerts: [] };
    await bootApp(data);
    click("tabMore");
    click("moreAnalytics");
    await flush();
    expect($("analyticsEmpty").hidden).toBe(false);

    data.analytics.coverage = [{ name: "A", posting_count: 1 }];
    click("tabArchived");
    click("tabMore");
    click("moreAnalytics");
    await flush();
    expect(text("usageByStep")).toContain("No Claude calls recorded yet.");
    expect(text("usageSummary")).toBe("Forecast this week: $0.00. Forecast this month: $0.00.");
    expect(text("careersPagesPerRunList")).toContain("No careers-page runs yet.");
    expect(text("discoverySearchesPerRunList")).toContain("No discovery runs yet.");
    expect(text("recentRunsList")).toContain("No runs yet.");
    expect(text("unconfirmedSummary")).toBe("0 postings not yet confirmed.");
  });

  test("a load error is reported", async () => {
    await bootApp(freshData(), { "GET /api/analytics": () => jsonResponse({ detail: "x" }, 500) });
    click("tabMore");
    click("moreAnalytics");
    await flush();
    expect(text("status")).toBe("Couldn't load analytics. x");
  });
});

describe("Proposals", () => {
  test("lists, approves and rejects proposals", async () => {
    const data = freshData();
    data.proposals = [
      { id: 1, kind: "company", payload: { action: "add", name: "Acme", reason: "good fit" } },
      { id: 2, kind: "source_retire", payload: { source_id: 5 } },
      { id: 3, kind: "x" },
    ];
    const { fetchMock } = await bootApp(data, {
      "POST /api/proposals/1/approve": () => { data.proposals.shift(); return jsonResponse({}); },
      "POST /api/proposals/2/reject": () => jsonResponse({ detail: "no" }, 400),
    });
    click("tabMore");
    click("moreProposals");
    await flush();
    expect(document.querySelectorAll("#proposalsList .row-item")).toHaveLength(3);
    expect($("proposalsList").textContent).toContain("good fit");
    buttonByText($("proposalsList"), "Approve").click();
    await flush();
    expect(text("status")).toBe("Approved.");
    expect(fetchMock.calls.some((c) => c.url === "/api/proposals/1/approve")).toBe(true);
    buttonByText($("proposalsList"), "Reject").click();
    await flush();
    expect(text("status")).toBe("Couldn't reject that proposal. no");
  });

  test("the empty note shows, and load and reject messages", async () => {
    await bootApp(freshData(), { "GET /api/proposals": () => jsonResponse({ detail: "x" }, 500) });
    click("tabMore");
    click("moreProposals");
    await flush();
    expect(text("status")).toBe("Couldn't load proposals. x");
  });

  test("the reject action says Rejected", async () => {
    const data = freshData();
    data.proposals = [{ id: 1, kind: "x" }];
    await bootApp(data, { "POST /api/proposals/1/reject": () => { data.proposals = []; return jsonResponse({}); } });
    click("tabMore");
    click("moreProposals");
    await flush();
    buttonByText($("proposalsList"), "Reject").click();
    await flush();
    expect(text("status")).toBe("Rejected.");
    expect($("proposalsEmpty").hidden).toBe(false);
  });
});

describe("Feedback", () => {
  const rows = () => [
    { id: 1, kind: "dead_link", message: "<b>dead</b>", status: "new", created_at: "2026-10-14T13:00:00Z", posting_id: 1, posting_company: "Amazon", posting_title: "PM Intern", issue_url: null },
    { id: 2, kind: "mystery", message: "m", status: "read", created_at: "2026-10-14T13:00:00Z", posting_id: null, issue_url: "https://github.com/x/issues/1" },
    { id: 3, kind: "bug", message: "b", status: "dismissed", created_at: "2026-10-14T13:00:00Z", posting_id: 999, issue_url: "javascript:x" },
  ];

  test("lists submissions as text, with new count, and Mark read / Dismiss work", async () => {
    const data = freshData();
    data.feedback = rows();
    const { fetchMock } = await bootApp(data, {
      "POST /api/feedback/1/status": (u, o) => {
        data.feedback[0].status = JSON.parse(o.body).status;
        return jsonResponse({});
      },
    });
    expect(text("nFeedback")).toBe("1");
    click("tabMore");
    click("moreFeedback");
    await flush();
    const first = $("feedbackList").querySelector(".row-item");
    expect(first.textContent).toContain("Dead link (new)");
    expect(first.querySelector("b")).toBeNull(); // stored text is never read as HTML
    expect(first.textContent).toContain("<b>dead</b>");

    buttonByText(first, "Mark read").click();
    await flush();
    expect(fetchMock.calls.find((c) => c.url === "/api/feedback/1/status").body).toEqual({ status: "read" });
    expect(text("nFeedback")).toBe("");
    expect($("feedbackList").querySelector(".row-item").textContent).toContain(", read");

    buttonByText($("feedbackList").querySelector(".row-item"), "Dismiss").click();
    await flush();
    expect($("feedbackList").querySelector(".row-item").textContent).toContain(", dismissed");
    expect($("feedbackList").textContent).toContain("GitHub issue");
  });

  test("Open as GitHub issue, and the card link jumps to the posting", async () => {
    const data = freshData();
    data.feedback = rows();
    const { fetchMock } = await bootApp(data, { "POST /api/feedback/1/issue": () => jsonResponse({}) });
    click("tabMore");
    click("moreFeedback");
    await flush();
    const first = $("feedbackList").querySelector(".row-item");
    first.querySelector("a").click();
    expect($("filter").value).toBe("Amazon");
    expect($("tabToday").getAttribute("aria-selected")).toBe("true");
    expect(cards()).toHaveLength(1);

    click("tabMore");
    click("moreFeedback");
    await flush();
    buttonByText($("feedbackList").querySelector(".row-item"), "Open as GitHub issue").click();
    await flush();
    expect(fetchMock.calls.some((c) => c.url === "/api/feedback/1/issue")).toBe(true);
  });

  test("a link to a posting that is gone says so; failures are reported", async () => {
    const data = freshData();
    data.feedback = rows();
    await bootApp(data, { "POST /api/feedback/1/status": () => jsonResponse({ detail: "no" }, 400) });
    click("tabMore");
    click("moreFeedback");
    await flush();
    const [first, , third] = $("feedbackList").querySelectorAll(".row-item");
    third.querySelector("a").click();
    expect(text("status")).toBe("That posting is no longer in the lists.");
    buttonByText(first, "Mark read").click();
    await flush();
    expect(text("status")).toBe("Couldn't save that. no");
  });

  test("a feedback load error is reported", async () => {
    await bootApp(freshData(), { "GET /api/feedback": () => jsonResponse({ detail: "x" }, 500) });
    click("tabMore");
    click("moreFeedback");
    await flush();
    expect(text("status")).toBe("Couldn't load feedback. x");
  });
});

describe("More menu", () => {
  test("opens, moves with the arrow keys, closes with Escape, Tab, and outside clicks", async () => {
    await bootApp(freshData());
    const key = (target, k) => target.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
    click("tabMore");
    expect($("moreMenu").hidden).toBe(false);
    expect($("tabMore").getAttribute("aria-expanded")).toBe("true");
    expect(document.activeElement.id).toBe("moreSources");
    key($("moreMenu"), "ArrowDown");
    expect(document.activeElement.id).toBe("moreAnalytics");
    key($("moreMenu"), "ArrowUp");
    key($("moreMenu"), "ArrowUp");
    expect(document.activeElement.id).toBe("moreFeedback");
    key($("moreMenu"), "Escape");
    expect($("moreMenu").hidden).toBe(true);
    expect(document.activeElement.id).toBe("tabMore");

    key($("tabMore"), "ArrowDown");
    expect($("moreMenu").hidden).toBe(false);
    key($("moreMenu"), "Tab");
    expect($("moreMenu").hidden).toBe(true);
    key($("tabMore"), "x");
    expect($("moreMenu").hidden).toBe(true);

    click("tabMore");
    click("tabMore");
    expect($("moreMenu").hidden).toBe(true);

    click("tabMore");
    document.body.click();
    expect($("moreMenu").hidden).toBe(true);
    document.body.click();

    click("tabMore");
    $("moreMenu").click();
    $("tabMore").click();
    $("tabMore").click();
    click("moreProposals");
    expect($("tabMore").getAttribute("aria-selected")).toBe("true");
    expect($("proposalsView").hidden).toBe(false);
  });
});
