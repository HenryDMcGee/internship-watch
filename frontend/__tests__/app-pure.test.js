const { freshData, bootApp, posting } = require("./app-fixtures");

let m;
beforeEach(async () => { m = (await bootApp(freshData())).mod; });

describe("dates", () => {
  test("fmtDate", () => {
    expect(m.fmtDate("")).toBe("");
    expect(m.fmtDate("junk")).toBe("junk");
    expect(m.fmtDate("2026-10-14")).toMatch(/Oct/);
  });

  test("fmtDateTime shows Central time", () => {
    expect(m.fmtDateTime("")).toBe("");
    expect(m.fmtDateTime("junk")).toBe("junk");
    expect(m.fmtDateTime("2026-10-14T18:30:00Z")).toMatch(/Oct 14, 1:30.*PM/);
  });
});

describe("formatting", () => {
  test("fmtPct, fmtUsd, fmtMin, fmtHrs", () => {
    expect(m.fmtPct(0.256)).toBe("26%");
    expect(m.fmtPct()).toBe("0%");
    expect(m.fmtUsd(1.5)).toBe("$1.50");
    expect(m.fmtUsd()).toBe("$0.00");
    expect(m.fmtMin(2)).toBe("2.0 min");
    expect(m.fmtHrs()).toBe("0.0 hours");
  });

  test("safeUrl", () => {
    expect(m.safeUrl("https://a.com")).toBe("https://a.com/");
    expect(m.safeUrl("javascript:1")).toBeNull();
    expect(m.safeUrl("http://[x")).toBeNull();
  });
});

describe("postings", () => {
  test("isOpen and matchesQuery", () => {
    expect(m.isOpen({ status: "closed" })).toBe(false);
    expect(m.isOpen({ status: "closing" })).toBe(true);
    const p = posting({ source_name: "Lever" });
    expect(m.matchesQuery(p, "")).toBe(true);
    expect(m.matchesQuery(p, "lever")).toBe(true);
    expect(m.matchesQuery(p, "seattle")).toBe(true);
    expect(m.matchesQuery({ company_name: "A", title: "B" }, "zzz")).toBe(false);
  });

  test("visibleRows hides closed and outside rows unless asked, and counts them", () => {
    const rows = [
      posting({ id: 1, status: "closed" }),
      posting({ id: 2 }),
      posting({ id: 3, location_match: "outside" }),
    ];
    const hidden = m.visibleRows(rows, { tab: "today", query: "", showClosed: false, showOutside: false });
    expect(hidden.rows.map((p) => p.id)).toEqual([2]);
    expect(hidden.closedHere).toBe(1);
    expect(hidden.outsideHere).toBe(1);

    const all = m.visibleRows(rows, { tab: "today", query: "", showClosed: true, showOutside: true });
    expect(all.rows.map((p) => p.id)).toEqual([2, 3, 1]); // open rows first

    const archived = m.visibleRows(rows, { tab: "archived", query: "", showClosed: false, showOutside: false });
    expect(archived.rows).toHaveLength(3);
    expect(archived.closedHere).toBe(0);

    const filtered = m.visibleRows(rows, { tab: "today", query: "nomatch", showClosed: true, showOutside: true });
    expect(filtered.rows).toHaveLength(0);
  });

  test("emptyStateFor", () => {
    expect(m.emptyStateFor("today", "x", 0).title).toBe("No postings match that filter");
    expect(m.emptyStateFor("today", "", 2).title).toBe("No open postings here");
    expect(m.emptyStateFor("today", "", 0).title).toBe("Nothing new yet today");
    expect(m.emptyStateFor("earlier", "", 0).title).toBe("No earlier postings");
    expect(m.emptyStateFor("archived", "", 0).title).toBe("Nothing archived");
  });
});

describe("search results", () => {
  test("briefSearchResult", () => {
    expect(m.briefSearchResult(null)).toBe("Done");
    expect(m.briefSearchResult({ agent_outcome: { errors: ["x"] } })).toBe("Done, with issues");
    expect(m.briefSearchResult({ agent_outcome: {}, new_count: 2 })).toBe("Done: 2 new");
    expect(m.briefSearchResult({})).toBe("Done: nothing new");
  });

  test("formatSearchResult", () => {
    expect(m.formatSearchResult(null)).toBe("Done.");
    expect(m.formatSearchResult({ run_status: "degraded", health: { failed_checks: [{ message: "a" }, { message: "b" }] } })).toBe("Degraded: a; b.");
    expect(m.formatSearchResult({ run_status: "degraded" })).toBe("Degraded: a health check failed.");
    expect(m.formatSearchResult({ agent_outcome: { steps_skipped: [{ step: "s", reason: "r" }], errors: ["e"] } }))
      .toBe("Finished, but some steps were skipped: s skipped (r); e.");
    expect(m.formatSearchResult({})).toBe("Done: nothing new.");
    expect(m.formatSearchResult({ new_count: 1, sources_found: 1 })).toBe("Done: 1 new posting, 1 source found.");
    expect(m.formatSearchResult({ new_count: 3 })).toBe("Done: 3 new postings, 0 sources found.");
  });

  test("searchErrorMessage", () => {
    expect(m.searchErrorMessage({ detail: "busy" }, 409)).toBe("busy");
    expect(m.searchErrorMessage({ detail: { message: "warn" } }, 409)).toBe("warn");
    expect(m.searchErrorMessage(null, 500)).toBe("Couldn't start the search (500).");
  });
});

describe("proposals and feedback labels", () => {
  test("proposalSummary", () => {
    expect(m.proposalSummary({ kind: "company", payload: { action: "add", name: "X" } })).toBe("Company: add — X");
    expect(m.proposalSummary({ kind: "company" })).toBe("Company: change — ");
    expect(m.proposalSummary({ kind: "source", payload: { name: "S", url: "u" } })).toBe("New source: S (u)");
    expect(m.proposalSummary({ kind: "source", payload: {} })).toBe("New source: ");
    expect(m.proposalSummary({ kind: "source_retire", payload: { source_id: 3 } })).toBe("Retire source #3");
    expect(m.proposalSummary({ kind: "careers_repair", payload: { company_name: "C", new_url: "n" } })).toBe("Replace careers page: C — n");
    expect(m.proposalSummary({ kind: "careers_repair", payload: {} })).toBe("Replace careers page:  — ");
    expect(m.proposalSummary({ kind: "other" })).toBe("other");
  });
});

describe("titles", () => {
  const doc = { exclude_words: ["Senior"], titles: [
    { title: "Product Manager", group: "Product", aliases: ["PM"], active: true },
    { title: "Strategy Intern", group: "Strategy", active: false },
  ] };

  test("normalizeTitleKey and titlesGroupsInOrder", () => {
    expect(m.normalizeTitleKey("  Product   MANAGER ")).toBe("product manager");
    expect(m.titlesGroupsInOrder(doc)).toEqual(["Product", "Strategy"]);
    expect(m.titlesGroupsInOrder(null)).toEqual([]);
  });

  test("findDuplicateTitleOwner looks at titles and aliases", () => {
    expect(m.findDuplicateTitleOwner(doc, "pm")).toBe("Product Manager");
    expect(m.findDuplicateTitleOwner(doc, "strategy  intern")).toBe("Strategy Intern");
    expect(m.findDuplicateTitleOwner(doc, "Brand New")).toBeNull();
  });

  test("findExcludedWordIn", () => {
    expect(m.findExcludedWordIn(doc, "Senior PM")).toBe("Senior");
    expect(m.findExcludedWordIn(doc, "PM")).toBeNull();
    expect(m.findExcludedWordIn({ titles: [] }, "PM")).toBeNull();
  });

  test("titleMatchesFilter", () => {
    expect(m.titleMatchesFilter(doc.titles[0], "")).toBe(true);
    expect(m.titleMatchesFilter(doc.titles[0], "pm")).toBe(true);
    expect(m.titleMatchesFilter(doc.titles[1], "pm")).toBe(false);
  });

  test("countTitlesChanges counts removed, added and toggled titles", () => {
    expect(m.countTitlesChanges(null, null)).toBe(0);
    const draft = JSON.parse(JSON.stringify(doc));
    draft.titles[0].active = false;
    draft.titles.push({ title: "New", group: "Product", active: true });
    draft.titles.splice(1, 1);
    expect(m.countTitlesChanges(doc, draft)).toBe(3);
    expect(m.countTitlesChanges(doc, doc)).toBe(0);
  });
});

describe("budgets", () => {
  test("buildBudgetsDoc turns numeric fields into numbers", () => {
    const values = { billing_mode: "api_key", daily_token_budget: "1500", model_scout: "haiku" };
    expect(m.buildBudgetsDoc(values)).toEqual({ billing_mode: "api_key", daily_token_budget: 1500, model_scout: "haiku" });
  });
});
