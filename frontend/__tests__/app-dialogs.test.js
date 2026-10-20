const { jsonResponse, freshData, bootApp, flush, $, click, type, buttonByText, budgetsBody } = require("./app-fixtures");

const text = (id) => $(id).textContent;

describe("Locations", () => {
  test("loads, saves, and closes", async () => {
    const data = freshData();
    data.locations = { countries: ["US", "JP"], include_remote: true };
    const { fetchMock } = await bootApp(data);
    click("openLocations");
    await flush();
    expect($("locJP").checked).toBe(true);
    expect($("locRemote").checked).toBe(true);
    $("locJP").checked = false;
    click("locationsSave");
    await flush();
    expect(fetchMock.calls.find((c) => c.method === "PUT" && c.url.endsWith("/locations")).body).toEqual({ countries: ["US"], include_remote: true });
    expect(text("locationsMsg")).toBe("Saved.");
    click("locationsClose");
    expect($("locationsDialog").hasAttribute("open")).toBe(false);
  });

  test("load and save errors are reported", async () => {
    await bootApp(freshData(), {
      "GET /api/settings/locations": () => jsonResponse({ detail: "a" }, 500),
      "PUT /api/settings/locations": () => jsonResponse({ detail: "b" }, 500),
    });
    click("openLocations");
    await flush();
    expect(text("locationsMsg")).toBe("Couldn't load locations. a");
    click("locationsSave");
    await flush();
    expect(text("locationsMsg")).toBe("Couldn't save. b");
  });
});

describe("Budgets", () => {
  const shareIds = ["bShareDiscovery", "bShareScout", "bShareClassification", "bShareImprovement"];
  const fieldError = (id) => document.querySelector('.field-error[data-for="' + id + '"]').textContent;
  const openBudgets = async (data, extra) => {
    const booted = await bootApp(data || freshData(), extra);
    click("openBudgets");
    await flush();
    return booted;
  };

  test("loads dollars, searches, percent shares, forecast, usage, history and the Browser Run table", async () => {
    const data = freshData();
    data.publicSite = { enabled: true };
    await openBudgets(data);
    expect($("bDailyUsd").value).toBe("2");
    expect($("bMonthlyUsd").value).toBe("20");
    expect($("bDailySearches").value).toBe("5");
    expect(shareIds.map((id) => $(id).value)).toEqual(["60", "20", "15", "5"]);
    expect($("bModelClassification").value).toBe("haiku");
    expect($("bSearchPerRun").value).toBe("3");
    expect($("bSearchPerCompany").value).toBe("1");
    expect($("bBrowserChecks").value).toBe("4");
    expect($("bFeedbackCap").value).toBe("20");
    expect(text("budgetsForecast")).toBe("This week: $1.25 · This month: $3.50");
    expect(text("budgetsUsage")).toBe("Today: $0.50 of $2.00 · This month: $3.00 of $20.00 · 1 of 5 searches used.");
    expect(text("budgetsLastReset")).toContain("Weekly count never reset.");
    expect(text("budgetsHistory")).toContain("daily_search_budget 4 → 5");
    expect(text("publicSiteStatus")).toBe("Today: 2 of 20 used · 7 board views");
    expect(text("browserRunPlan")).toBe("Plan: Free.");
    expect(text("browserRunToday")).toContain("1.0 min of 10.0 min used");
    expect(text("browserRunBackupToday")).toBe("Backup today: 1 of 5 pages used (blocked).");
    expect($("browserRunMonth").hidden).toBe(true);
    expect(document.querySelector("#browserRunRunsBody tr").children).toHaveLength(5);
    expect($("locPublic").checked).toBe(true);
    expect(text("sharesTotal")).toBe("Total: 100% ✓");
    expect($("sharesTotal").classList.contains("complete")).toBe(true);
    expect($("budgetsSave").disabled).toBe(false);
    expect(text("budgetsMsg")).toBe("");
  });

  test("the Billing section, the token inputs and the Browser Run chart are gone", async () => {
    await openBudgets();
    for (const id of ["bBillingMode", "bDailyTokens", "bWeeklyTokens", "browserRunChart"]) expect($(id)).toBeNull();
    expect(document.querySelectorAll("#budgetsDialog .br-chart, #budgetsDialog .br-bar-col, #budgetsDialog .br-limit-line")).toHaveLength(0);
    const dialogText = $("budgetsDialog").textContent;
    expect(dialogText).not.toMatch(/[Bb]illing|[Tt]oken/);
    expect(dialogText).not.toContain("Weekly");
    // Exactly three inputs under Limits: Daily $, Monthly $, Daily searches.
    const limits = Array.from($("budgetsLimits").querySelectorAll("input")).map((i) => i.id);
    expect(limits).toEqual(["bDailyUsd", "bMonthlyUsd", "bDailySearches"]);
    // Rarely changed limits sit in a collapsed Advanced section.
    const advanced = $("budgetsAdvanced");
    expect(advanced.tagName).toBe("DETAILS");
    expect(advanced.open).toBe(false);
    expect(Array.from(advanced.querySelectorAll("input")).map((i) => i.id)).toEqual(["bSearchPerRun", "bSearchPerCompany", "bBrowserChecks"]);
    // One row per step: name, percent input, model select.
    const rows = document.querySelectorAll("#budgetsDialog .share-row");
    expect(rows).toHaveLength(4);
    expect(rows[0].textContent).toContain("Discovery");
    expect(rows[0].querySelector("input").id).toBe("bShareDiscovery");
    expect(rows[0].querySelector("select").id).toBe("bModelDiscovery");
    expect($("sharesEvenSplit").textContent).toBe("Even split");
    // The Browser Run table and footnote stay.
    expect($("browserRunRunsTable")).not.toBeNull();
    expect(dialogText).toContain("Cloudflare's own dashboard");
  });

  test("the total pill at 90%, 100% and over 100%, and Even split", async () => {
    await openBudgets();
    type("bShareClassification", "5");
    expect(text("sharesTotal")).toBe("Total: 90% — 10% left to assign");
    expect($("sharesTotal").classList.contains("complete")).toBe(false);
    expect($("sharesTotal").classList.contains("incomplete")).toBe(true);
    expect($("budgetsSave").disabled).toBe(true);
    expect(text("budgetsMsg")).toBe("Fix the flagged fields to save.");

    type("bShareClassification", "25");
    expect(text("sharesTotal")).toBe("Total: 110% — 10% over");
    expect($("sharesTotal").classList.contains("incomplete")).toBe(true);
    expect($("budgetsSave").disabled).toBe(true);

    type("bShareClassification", "15");
    expect(text("sharesTotal")).toBe("Total: 100% ✓");
    expect($("sharesTotal").classList.contains("complete")).toBe(true);
    expect($("budgetsSave").disabled).toBe(false);
    expect(text("budgetsMsg")).toBe("");

    type("bShareDiscovery", "10");
    expect($("budgetsSave").disabled).toBe(true);
    click("sharesEvenSplit");
    expect(shareIds.map((id) => $(id).value)).toEqual(["25", "25", "25", "25"]);
    expect(text("sharesTotal")).toBe("Total: 100% ✓");
    expect($("budgetsSave").disabled).toBe(false);
  });

  test("invalid values are flagged next to the field as you type, and Save waits for them", async () => {
    const { fetchMock } = await openBudgets();
    type("bDailyUsd", "-1");
    expect(fieldError("bDailyUsd")).toBe("Must be 0 or more.");
    expect($("bDailyUsd").getAttribute("aria-invalid")).toBe("true");
    expect($("budgetsSave").disabled).toBe(true);
    expect(text("budgetsMsg")).toBe("Fix the flagged fields to save.");
    click("budgetsSave");
    await flush();
    expect(fetchMock.calls.some((c) => c.method === "PUT" && c.url === "/api/budgets")).toBe(false);

    type("bDailyUsd", "abc");
    expect(fieldError("bDailyUsd")).toBe("Enter a number.");
    type("bDailyUsd", "");
    expect(fieldError("bDailyUsd")).toBe("Enter a number.");
    type("bDailyUsd", "3");
    expect(fieldError("bDailyUsd")).toBe("");
    expect($("bDailyUsd").hasAttribute("aria-invalid")).toBe(false);
    expect($("budgetsSave").disabled).toBe(false);
    expect(text("budgetsMsg")).toBe("");

    type("bFeedbackCap", "2.5");
    expect(fieldError("bFeedbackCap")).toBe("Enter a whole number.");
    expect($("budgetsSave").disabled).toBe(true);
    type("bFeedbackCap", "30");
    expect($("budgetsSave").disabled).toBe(false);

    type("bShareScout", "120");
    expect(fieldError("bShareScout")).toBe("Must be 100 or less.");
    expect($("budgetsSave").disabled).toBe(true);
    type("bShareScout", "20");
    expect($("budgetsSave").disabled).toBe(false);

    type("bSearchPerRun", "-2");
    expect(fieldError("bSearchPerRun")).toBe("Must be 0 or more.");
    expect($("budgetsSave").disabled).toBe(true);
    type("bSearchPerRun", "9");
    expect($("budgetsSave").disabled).toBe(false);
  });

  test("Save sends decimals for the shares, the feedback cap, and none of the removed fields", async () => {
    const data = freshData();
    const { fetchMock } = await openBudgets(data);
    expect($("locPublic").checked).toBe(false);
    $("locPublic").checked = true;
    type("bDailyUsd", "2.5");
    type("bShareDiscovery", "50");
    type("bShareScout", "30");
    type("bFeedbackCap", "40");
    click("budgetsSave");
    await flush();
    const put = fetchMock.calls.find((c) => c.method === "PUT" && c.url === "/api/budgets");
    expect(put.body.doc).toEqual({
      daily_usd_budget: 2.5, monthly_usd_budget: 20, daily_search_budget: 5,
      search_limit_per_run: 3, search_limit_per_company: 1, browser_checks_per_run: 4,
      public_feedback_daily_limit: 40,
      share_discovery: 0.5, share_scout: 0.3, share_classification: 0.15, share_improvement: 0.05,
      model_discovery: "sonnet", model_scout: "sonnet", model_classification: "haiku", model_improvement: "sonnet",
    });
    expect(Object.keys(put.body.doc).join(" ")).not.toMatch(/billing|token/);
    expect(fetchMock.calls.find((c) => c.url === "/api/settings/public-site" && c.method === "PUT").body).toEqual({ enabled: true });
    expect(text("budgetsMsg")).toBe("Saved. The public page updates within 5 minutes.");
    click("budgetsClose");
    click("openBudgets");
    await flush();
    expect($("locPublic").checked).toBe(true);
  });

  test("a paid month, and empty history, runs and public site block", async () => {
    const data = freshData();
    const b = budgetsBody();
    b.resets = { daily: null, weekly: { reset_at: "2026-10-13T13:00:00Z" } };
    b.changes = [];
    b.public_site = undefined;
    b.browser_run.plan = "paid";
    b.browser_run.month = { hours_used: 1, hours_included: 10, hours_left: 9, overage_usd: 2 };
    b.browser_run.backup_today = null;
    b.browser_run.today.minutes_budget = 0;
    b.browser_run.last_runs = [];
    data.budgets = b;
    await openBudgets(data);
    expect(text("budgetsUsage")).toContain("$0.50 of $2.00");
    expect(text("budgetsLastReset")).toContain("Daily count never reset.");
    expect(text("budgetsHistory")).toBe("No changes yet.");
    expect(text("publicSiteStatus")).toBe("Today: 0 of 20 used · 0 board views");
    expect(text("browserRunPlan")).toBe("Plan: Paid.");
    expect($("browserRunMonth").hidden).toBe(false);
    expect(text("browserRunMonth")).toContain("Estimated overage: $2.00.");
    expect(text("browserRunBackupToday")).toBe("Backup today: 0 of 0 pages used.");
    expect(text("browserRunRunsBody")).toBe("No runs recorded yet.");
  });

  test("a response without usage, forecast or Browser Run sections is fine", async () => {
    const data = freshData();
    data.budgets = Object.assign(budgetsBody(), { browser_run: undefined, usage: undefined, forecast: undefined, changes: undefined, resets: undefined, public_site: undefined });
    await openBudgets(data);
    expect(text("budgetsUsage")).toBe("Today: $0.00 of $2.00 · This month: $0.00 of $20.00 · 0 of 5 searches used.");
    expect(text("budgetsForecast")).toBe("This week: $0.00 · This month: $0.00");
  });

  test("a load error is reported, and Save on an empty form asks for the fields first", async () => {
    const { fetchMock } = await openBudgets(freshData(), { "GET /api/budgets": () => jsonResponse({ detail: "a" }, 500) });
    expect(text("budgetsMsg")).toBe("Couldn't load budgets. a");
    click("budgetsSave");
    await flush();
    expect(text("budgetsMsg")).toBe("Fix the flagged fields to save.");
    expect($("budgetsSave").disabled).toBe(true);
    expect(fieldError("bDailyUsd")).toBe("Enter a number.");
    expect(fetchMock.calls.some((c) => c.method === "PUT")).toBe(false);
  });

  test("a save the server refuses is reported, and Save stays usable", async () => {
    await openBudgets(freshData(), { "PUT /api/budgets": () => jsonResponse({ detail: "b" }, 400) });
    click("budgetsSave");
    await flush();
    expect(text("budgetsMsg")).toBe("Couldn't save. b");
    expect($("budgetsSave").disabled).toBe(false);
  });

  test("reset asks first, then resets the daily or weekly count", async () => {
    const { fetchMock } = await openBudgets();
    window.confirm = jest.fn(() => false);
    click("resetDailyBudget");
    expect(window.confirm.mock.calls[0][0]).toContain("Today: $0.50 of $2.00 and 1 of 5 searches used.");
    expect(fetchMock.calls.some((c) => c.url.endsWith("/resets"))).toBe(false);

    window.confirm = jest.fn(() => true);
    click("resetDailyBudget");
    await flush();
    expect(text("budgetsMsg")).toBe("Today's usage was reset.");
    click("resetWeeklyBudget");
    await flush();
    expect(window.confirm.mock.calls[1][0]).toContain("This week: $1.00 used.");
    expect(text("budgetsMsg")).toBe("This week's usage was reset.");
    expect(fetchMock.calls.filter((c) => c.url.endsWith("/resets")).map((c) => c.body.scope)).toEqual(["daily", "weekly"]);
  });

  test("reset errors, and reset before the budgets ever loaded", async () => {
    await bootApp(freshData(), { "POST /api/budgets/resets": () => jsonResponse({ detail: "no" }, 400) });
    window.confirm = jest.fn(() => true);
    click("resetWeeklyBudget");
    await flush();
    expect(window.confirm.mock.calls[0][0]).toContain("This week: $0.00 used.");
    expect(text("budgetsMsg")).toBe("Couldn't reset. no");
    click("resetDailyBudget");
    await flush();
    expect(window.confirm.mock.calls[1][0]).toContain("Today: $0.00 of $0.00 and 0 of 0 searches used.");
  });
});

describe("Titles", () => {
  const rowFor = (name) => Array.from(document.querySelectorAll("#titlesBody .title-row")).find((r) => r.textContent.includes(name));

  async function openTitles(data, extra) {
    const app = await bootApp(data || freshData(), extra);
    click("openTitles");
    await flush();
    return app;
  }

  test("shows groups, aliases and counts", async () => {
    await openTitles();
    expect(document.querySelectorAll("#titlesBody .titles-group")).toHaveLength(2);
    expect(text("titlesBody")).toContain("2 on, 0 off".replace("2 on, 0 off", "1 on, 1 off"));
    expect(rowFor("Product Manager Intern").textContent).toContain("Also: PM Intern");
    expect(rowFor("Associate Product Manager").className).toContain("off");
    expect(text("titlesUnsaved")).toBe("No unsaved changes.");
    expect($("titlesSave").disabled).toBe(true);
    expect($("titlesNewGroupName").hidden).toBe(true);
  });

  test("a pending pull request is linked", async () => {
    const data = freshData();
    data.pendingTitles = { pending: { html_url: "https://github.com/x/pull/3", number: 3, title: "Add" } };
    await openTitles(data);
    expect($("titlesPending").hidden).toBe(false);
    expect(text("titlesPendingLink")).toBe("Title changes waiting for review: #3 (Add)");
    data.pendingTitles = { pending: { number: null } };
    click("titlesClose");
    click("openTitles");
    await flush();
    expect(text("titlesPendingLink")).toBe("Title changes waiting for review: #");
  });

  test("a failed titles load is reported; a failed pending lookup is ignored", async () => {
    await openTitles(freshData(), { "GET /api/titles": () => jsonResponse({ detail: "x" }, 500) });
    expect(text("titlesBody")).toBe("Couldn't load titles. x");
    click("titlesClose");
    await openTitles(freshData(), { "GET /api/titles/pending": () => jsonResponse({}, 500) });
    expect($("titlesPending").hidden).toBe(true);
  });

  test("toggling and deleting a title counts as unsaved changes", async () => {
    await openTitles();
    const cb = rowFor("Associate Product Manager").querySelector("input");
    cb.checked = true;
    cb.dispatchEvent(new Event("change"));
    expect(text("titlesUnsaved")).toBe("1 unsaved change.");
    expect($("titlesSave").disabled).toBe(false);

    rowFor("Strategy Intern").querySelector(".title-x").click();
    expect(rowFor("Strategy Intern").textContent).toContain("Delete 'Strategy Intern'?");
    buttonByText(rowFor("Strategy Intern"), "Cancel").click();
    expect(rowFor("Strategy Intern").querySelector("input")).not.toBeNull();
    rowFor("Strategy Intern").querySelector(".title-x").click();
    buttonByText(rowFor("Strategy Intern"), "Delete").click();
    expect(rowFor("Strategy Intern")).toBeUndefined();
    expect(text("titlesUnsaved")).toBe("2 unsaved changes.");
  });

  test("filtering the list", async () => {
    await openTitles();
    type("titlesFilter", "strategy");
    expect(document.querySelectorAll("#titlesBody .titles-group")).toHaveLength(1);
    type("titlesFilter", "zzz");
    expect(text("titlesBody")).toContain("No titles match that filter.");
  });

  test("adding a title validates the input", async () => {
    await openTitles();
    const msg = () => text("titlesAddMsg");
    click("titlesAddBtn");
    expect(msg()).toBe("Enter a title.");
    type("titlesNewTitle", "x".repeat(121));
    click("titlesAddBtn");
    expect(msg()).toBe("Titles must be 120 characters or fewer.");
    type("titlesNewTitle", "Growth Intern");
    $("titlesNewGroup").value = "__new__";
    $("titlesNewGroup").dispatchEvent(new Event("change"));
    expect($("titlesNewGroupName").hidden).toBe(false);
    click("titlesAddBtn");
    expect(msg()).toBe("Enter a group name.");
    type("titlesNewGroupName", "Growth");
    type("titlesNewTitle", "pm intern");
    click("titlesAddBtn");
    expect(msg()).toBe("'pm intern' is already in the list, under 'Product Manager Intern'.");
    type("titlesNewTitle", "Senior Growth");
    click("titlesAddBtn");
    expect(msg()).toBe("'Senior Growth' contains the excluded word 'senior'.");
  });

  test("adding to an existing group and to a new group", async () => {
    await openTitles();
    type("titlesNewTitle", "Growth Intern");
    $("titlesNewGroup").value = "Strategy";
    click("titlesAddBtn");
    expect(rowFor("Growth Intern")).toBeTruthy();
    expect($("titlesNewTitle").value).toBe("");
    expect(text("titlesUnsaved")).toBe("1 unsaved change.");

    type("titlesNewTitle", "Data Intern");
    $("titlesNewGroup").value = "__new__";
    $("titlesNewGroup").dispatchEvent(new Event("change"));
    type("titlesNewGroupName", "Data");
    $("titlesNewGroupName").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", cancelable: true }));
    expect(rowFor("Data Intern")).toBeTruthy();
    expect($("titlesNewGroup").value).toBe("Data");
    expect($("titlesNewGroupName").hidden).toBe(true);

    type("titlesNewTitle", "Ops Intern");
    $("titlesNewTitle").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", cancelable: true }));
    $("titlesNewTitle").dispatchEvent(new KeyboardEvent("keydown", { key: "a", cancelable: true }));
    $("titlesNewGroupName").dispatchEvent(new KeyboardEvent("keydown", { key: "a", cancelable: true }));
    expect(rowFor("Ops Intern")).toBeTruthy();
  });

  test("applying JSON, including bad JSON", async () => {
    await openTitles();
    type("titlesJson", "{nope");
    click("titlesJsonApply");
    expect(text("titlesMsg")).toMatch(/That's not valid JSON/);
    type("titlesJson", JSON.stringify({ exclude_words: [], titles: [{ title: "Only", group: "G", aliases: [], active: true }] }));
    click("titlesJsonApply");
    expect(text("titlesMsg")).toMatch(/^Applied/);
    expect(rowFor("Only")).toBeTruthy();
    expect(text("titlesUnsaved")).toBe("4 unsaved changes.");
  });

  test("saving opens a pull request link and clears unsaved changes", async () => {
    const { fetchMock } = await openTitles();
    rowFor("Strategy Intern").querySelector("input").checked = false;
    rowFor("Strategy Intern").querySelector("input").dispatchEvent(new Event("change"));
    click("titlesSave");
    await flush();
    expect(text("titlesMsg")).toBe("Saved. Review and merge the pull request to apply it.");
    expect(text("titlesPendingLink")).toContain("#9 (Titles)");
    expect(text("titlesUnsaved")).toBe("No unsaved changes.");
    expect(fetchMock.calls.find((c) => c.method === "PUT").body.doc.titles).toHaveLength(3);
  });

  test("a save error keeps the button enabled; saving before load does nothing", async () => {
    await openTitles(freshData(), { "PUT /api/titles": () => jsonResponse({ detail: "no" }, 400) });
    rowFor("Strategy Intern").querySelector("input").checked = false;
    rowFor("Strategy Intern").querySelector("input").dispatchEvent(new Event("change"));
    click("titlesSave");
    await flush();
    expect(text("titlesMsg")).toBe("Couldn't save. no");
    expect($("titlesSave").disabled).toBe(false);
  });

  test("closing with unsaved changes asks first", async () => {
    await openTitles();
    rowFor("Strategy Intern").querySelector("input").checked = false;
    rowFor("Strategy Intern").querySelector("input").dispatchEvent(new Event("change"));
    window.confirm = jest.fn(() => false);
    click("titlesClose");
    expect($("titlesDialog").hasAttribute("open")).toBe(true);
    const cancel = new Event("cancel", { cancelable: true });
    $("titlesDialog").dispatchEvent(cancel);
    expect(cancel.defaultPrevented).toBe(true);

    const unload = new Event("beforeunload", { cancelable: true });
    $("titlesDialog").open = true;
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);

    window.confirm = jest.fn(() => true);
    const cancel2 = new Event("cancel", { cancelable: true });
    $("titlesDialog").dispatchEvent(cancel2);
    expect(cancel2.defaultPrevented).toBe(false);
    click("titlesClose");
    expect($("titlesDialog").hasAttribute("open")).toBe(false);
  });

  test("closing without changes does not ask", async () => {
    await openTitles();
    window.confirm = jest.fn();
    click("titlesClose");
    expect(window.confirm).not.toHaveBeenCalled();
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
  });

  test("the JSON box is left alone while it has focus", async () => {
    await openTitles();
    $("titlesJsonDetails").setAttribute("open", "");
    $("titlesJson").focus();
    type("titlesJson", "typing");
    type("titlesFilter", "a");
    $("titlesJson").focus();
    type("titlesFilter", "");
    expect($("titlesJson").value).toBeTruthy();
  });
});

describe("Companies", () => {
  const companies = () => [
    { id: 1, name: "Zed", status: "review", careers_url: "https://z.com", feed_type: "greenhouse" },
    { id: 2, name: "Amazon", status: "ranked", rank: 1, careers_url: "https://a.com", last_feed_status: "error", last_feed_error: "boom" },
    { id: 3, name: "Beta", status: "pinned", last_feed_status: "no_source" },
    { id: 4, name: "Cee", status: "ranked", rank: 2, careers_url: "https://c.com" },
    { id: 5, name: "Gone", status: "removed" },
  ];

  async function openCompanies(extra) {
    const data = freshData();
    data.companies = companies();
    const app = await bootApp(data, extra);
    click("openCompanies");
    await flush();
    return app;
  }

  test("sections, details and removed toggle", async () => {
    await openCompanies();
    expect($("reviewSection").hidden).toBe(false);
    expect(document.querySelectorAll("#activeList .row-item")).toHaveLength(3);
    expect(text("activeList")).toContain("Rank 1");
    expect(text("activeList")).toContain("feed error: boom");
    expect(text("activeList")).toContain("no source yet");
    expect(text("reviewList")).toContain("greenhouse");
    expect(text("activeList")).toContain("careers link on file");
    expect($("removedSection").hidden).toBe(false);
    expect($("removedList").hidden).toBe(true);
    click("toggleRemoved");
    expect($("removedList").hidden).toBe(false);
    expect(text("toggleRemoved")).toBe("Hide removed companies");
    click("toggleRemoved");
    expect(text("toggleRemoved")).toBe("Show removed companies");
    click("companiesClose");
  });

  test("pin, remove and restore", async () => {
    const { fetchMock } = await openCompanies({
      "POST /api/companies/1/pin": () => jsonResponse({}),
      "POST /api/companies/1/unpin": () => jsonResponse({}),
      "POST /api/companies/5/pin": () => jsonResponse({ detail: "no" }, 400),
      "POST /api/companies/3/unpin": () => jsonResponse({ detail: "no" }, 400),
    });
    buttonByText($("reviewList"), "Pin").click();
    await flush();
    buttonByText($("reviewList"), "Remove").click();
    await flush();
    expect(fetchMock.calls.filter((c) => c.url.startsWith("/api/companies/1/")).map((c) => c.url)).toEqual(["/api/companies/1/pin", "/api/companies/1/unpin"]);
    buttonByText($("removedList"), "Restore").click();
    await flush();
    expect(text("companiesMsg")).toBe("Couldn't update Gone. no");
    Array.from($("activeList").querySelectorAll("button")).filter((b) => b.textContent === "Remove")[2].click();
    await flush();
    expect(text("companiesMsg")).toBe("Couldn't update Beta. no");
  });

  test("Check now polls until done, failed, or timed out", async () => {
    jest.useFakeTimers();
    try {
      let n = 0;
      await openCompanies({
        "POST /api/companies/check": () => jsonResponse({ id: 8 }),
        "GET /api/companies/check/8": () => {
          n += 1;
          if (n === 1) return jsonResponse({ status: "running" });
          return jsonResponse({ status: "completed", result: { new_count: 1 } });
        },
      });
      await jest.advanceTimersByTimeAsync(10);
      const status = $("activeList").querySelector(".check-status");
      buttonByText($("activeList"), "Check now").click();
      await jest.advanceTimersByTimeAsync(10);
      expect(status.textContent).toBe("Running…");
      await jest.advanceTimersByTimeAsync(5100);
      expect(status.textContent).toBe("Done, 1 new posting.");
    } finally {
      jest.useRealTimers();
    }
  });

  test("Check now outcomes: several, none, failure, error, never finishing, start error", async () => {
    jest.useFakeTimers();
    try {
      const results = [
        { status: "completed", result: { new_count: 3 } },
        { status: "completed", result: {} },
        { status: "failed", result: { error: "bad" } },
        { status: "failed" },
        { status: "running" },
      ];
      let idx = 0;
      let current = null;
      let startFails = false;
      await openCompanies({
        "POST /api/companies/check": () => (startFails ? jsonResponse({ detail: "no" }, 400) : jsonResponse({ id: 8 })),
        "GET /api/companies/check/8": () => (current === "net" ? jsonResponse({ detail: "down" }, 500) : jsonResponse(current)),
      });
      await jest.advanceTimersByTimeAsync(10);
      const status = $("activeList").querySelector(".check-status");
      const run = async (ms) => {
        buttonByText($("activeList"), "Check now").click();
        await jest.advanceTimersByTimeAsync(ms);
      };
      const expected = ["Done, 3 new postings.", "Done.", "Check failed: bad", "Check failed.", "Still running. Check back later."];
      for (const r of results) {
        current = r;
        await run(r.status === "running" ? 5000 * 22 : 50);
        expect(status.textContent).toBe(expected[idx++]);
      }
      current = "net";
      await run(50);
      expect(status.textContent).toBe("Couldn't get the check's status. down");
      startFails = true;
      await run(50);
      expect(status.textContent).toBe("Couldn't start the check. no");
    } finally {
      jest.useRealTimers();
    }
  });

  test("a companies load error", async () => {
    const data = freshData();
    await bootApp(data, { "GET /api/companies": () => jsonResponse({ detail: "x" }, 500) });
    click("openCompanies");
    await flush();
    expect(text("companiesMsg")).toBe("Couldn't load companies. x");
  });

  test("empty lists", async () => {
    await bootApp(freshData());
    click("openCompanies");
    await flush();
    expect(text("activeList")).toBe("Nothing on the list yet.");
    expect($("reviewSection").hidden).toBe(true);
  });

  test("add a company", async () => {
    const { fetchMock } = await openCompanies({ "POST /api/companies": () => jsonResponse({}) });
    click("companyAddBtn");
    expect(text("companiesMsg")).toBe("Enter a company name.");
    type("companyName", "Newco");
    click("companyAddBtn");
    await flush();
    expect(fetchMock.calls.find((c) => c.method === "POST" && c.url === "/api/companies").body).toEqual({ name: "Newco", careers_url: null });
    expect(text("companiesMsg")).toBe("Added Newco.");
    type("companyName", "Other");
    type("companyUrl", "https://o.com");
    click("companyAddBtn");
    await flush();
    expect(fetchMock.calls.filter((c) => c.method === "POST" && c.url === "/api/companies")[1].body.careers_url).toBe("https://o.com");
  });

  test("add a company error", async () => {
    await openCompanies({ "POST /api/companies": () => jsonResponse({ detail: "dup" }, 409) });
    type("companyName", "Newco");
    click("companyAddBtn");
    await flush();
    expect(text("companiesMsg")).toBe("Couldn't add Newco. dup");
  });

  test("paste a list: preview then save", async () => {
    const preview = [
      { raw: "Acme", name: "Acme", match: "new", url: "https://acme.com" },
      { raw: "Amazon", name: "Amazon", match: "existing" },
      { raw: "???", match: "new" },
    ];
    const { fetchMock } = await openCompanies({
      "POST /api/companies/paste/preview": () => jsonResponse({ entries: preview }),
      "POST /api/companies/paste/save": () => jsonResponse({ saved: [{}, {}] }),
    });
    click("pastePreviewBtn");
    expect(text("companiesMsg")).toBe("Paste at least one company.");
    type("pasteText", "Acme\nAmazon\n???");
    click("pastePreviewBtn");
    await flush();
    expect(document.querySelectorAll("#pastePreviewResult tbody tr")).toHaveLength(3);
    expect(document.querySelectorAll("#pastePreviewResult input")).toHaveLength(2);
    buttonByText($("pastePreviewResult"), "Save selected").click();
    await flush();
    const saved = fetchMock.calls.find((c) => c.url.endsWith("/paste/save")).body.entries;
    expect(saved).toEqual([{ name: "Acme", url: "https://acme.com" }, { name: undefined, url: null }]);
    expect(text("companiesMsg")).toBe("Saved 2 companies.");
    expect($("pasteText").value).toBe("");
  });

  test("paste: nothing selected, one saved, and errors", async () => {
    let mode = "ok";
    await openCompanies({
      "POST /api/companies/paste/preview": () => (mode === "err" ? jsonResponse({ detail: "no" }, 400) : jsonResponse({ entries: mode === "empty" ? [] : [{ raw: "Acme", name: "Acme", match: "new" }] })),
      "POST /api/companies/paste/save": () => (mode === "saveerr" ? jsonResponse({ detail: "bad" }, 400) : jsonResponse({ saved: [{}] })),
    });
    type("pasteText", "Acme");
    click("pastePreviewBtn");
    await flush();
    document.querySelector("#pastePreviewResult input").checked = false;
    buttonByText($("pastePreviewResult"), "Save selected").click();
    await flush();
    expect(text("companiesMsg")).toBe("Nothing selected to save.");
    document.querySelector("#pastePreviewResult input").checked = true;
    mode = "saveerr";
    buttonByText($("pastePreviewResult"), "Save selected").click();
    await flush();
    expect(text("companiesMsg")).toBe("Couldn't save. bad");
    mode = "ok";
    buttonByText($("pastePreviewResult"), "Save selected").click();
    await flush();
    expect(text("companiesMsg")).toBe("Saved 1 company.");
    mode = "empty";
    click("pastePreviewBtn");
    await flush();
    mode = "err";
    type("pasteText", "Acme");
    click("pastePreviewBtn");
    await flush();
    expect(text("companiesMsg")).toBe("Couldn't preview that list. no");
  });
});
