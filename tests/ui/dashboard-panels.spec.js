const { test, expect, resetData, DASHBOARD, PUBLIC } = require("./fixtures");

test.beforeEach(async ({ request }) => resetData(request));

async function openMore(page, id) {
  await page.goto("/");
  await page.click("#tabMore");
  await page.click(id);
}

test.describe("Budgets panel", () => {
  test("the Public site switch loads, saves and round-trips", async ({ page, browser, request }) => {
    await page.goto("/");
    await page.click("#openBudgets");
    await expect(page.locator("#budgetsDialog")).toBeVisible();
    await expect(page.locator("#bDailyTokens")).not.toHaveValue("");
    await expect(page.locator("#locPublic")).not.toBeChecked();

    // Off: the public page says it isn't available.
    const off = await browser.newPage();
    await off.goto(PUBLIC + "/");
    await expect(off.locator("#unavailable")).toBeVisible();
    await off.close();

    await page.check("#locPublic");
    await page.click("#budgetsSave");
    await expect(page.locator("#budgetsMsg")).toHaveText("Saved. The public page updates within 5 minutes.");
    expect(await (await request.get(DASHBOARD + "/api/settings/public-site")).json()).toEqual({ enabled: true });

    // On: a new visitor (the public API is cached for 5 minutes per browser) sees the board.
    const visitor = await browser.newPage();
    await visitor.goto(PUBLIC + "/");
    await expect(visitor.locator("#board")).toBeVisible();
    await visitor.close();

    // Close, reload the dashboard, and the switch is still on.
    await page.click("#budgetsClose");
    await expect(page.locator("#budgetsDialog")).toBeHidden();
    await page.reload();
    await page.click("#openBudgets");
    await expect(page.locator("#locPublic")).toBeChecked();

    await page.uncheck("#locPublic");
    await page.click("#budgetsSave");
    await expect(page.locator("#budgetsMsg")).toContainText("Saved.");
    expect(await (await request.get(DASHBOARD + "/api/settings/public-site")).json()).toEqual({ enabled: false });
  });

  test("budget numbers save and show in the history", async ({ page }) => {
    await page.goto("/");
    await page.click("#openBudgets");
    await expect(page.locator("#budgetsHistory")).toHaveText("No changes yet.");
    await page.fill("#bDailySearches", "7");
    await page.fill("#bDailyTokens", "123456");
    await page.selectOption("#bBillingMode", "api_key");
    await page.click("#budgetsSave");
    await expect(page.locator("#budgetsMsg")).toContainText("Saved.");
    await expect(page.locator("#budgetsHistory")).toContainText("daily_search_budget");
    await expect(page.locator("#budgetsUsage")).toContainText("of $");
    await page.reload();
    await page.click("#openBudgets");
    await expect(page.locator("#bDailySearches")).toHaveValue("7");
    await expect(page.locator("#bDailyTokens")).toHaveValue("123456");
    await expect(page.locator("#bBillingMode")).toHaveValue("api_key");
  });

  test("a number the server refuses is reported", async ({ page }) => {
    await page.goto("/");
    await page.click("#openBudgets");
    await page.fill("#bDailySearches", "-5");
    await page.click("#budgetsSave");
    await expect(page.locator("#budgetsMsg")).toContainText("Couldn't save.");
    await expect(page.locator("#budgetsSave")).toBeEnabled();
  });

  test("shows the forecast, usage and Browser Run numbers", async ({ page }) => {
    await page.goto("/");
    await page.click("#openBudgets");
    await expect(page.locator("#budgetsForecast")).toContainText("Forecast this week:");
    await expect(page.locator("#budgetsUsage")).toContainText("Today:");
    await expect(page.locator("#budgetsLastReset")).toHaveText("Daily count never reset. Weekly count never reset.");
    await expect(page.locator("#browserRunPlan")).toHaveText("Plan: Free.");
    await expect(page.locator("#browserRunToday")).toContainText("min of");
    await expect(page.locator("#browserRunCloudflareFree")).toContainText("Cloudflare free allowance");
    await expect(page.locator("#browserRunBackupToday")).toContainText("Backup today: 2 of");
    await expect(page.locator("#browserRunChart .br-bar-col")).not.toHaveCount(0);
    await expect(page.locator("#browserRunRunsBody tr")).not.toHaveCount(0);
    await expect(page.locator("#browserRunRunsBody")).toContainText("4");
  });

  test("reset asks first, and only then zeroes the count", async ({ page }) => {
    await page.goto("/");
    await page.click("#openBudgets");
    const messages = [];
    page.on("dialog", async (dialog) => {
      messages.push(dialog.message());
      if (messages.length === 1) await dialog.dismiss(); else await dialog.accept();
    });
    await page.click("#resetDailyBudget");
    await expect(page.locator("#budgetsMsg")).toHaveText("");
    await page.click("#resetDailyBudget");
    await expect(page.locator("#budgetsMsg")).toHaveText("Today's usage was reset.");
    await page.click("#resetWeeklyBudget");
    await expect(page.locator("#budgetsMsg")).toHaveText("This week's usage was reset.");
    await expect(page.locator("#budgetsLastReset")).toContainText("Daily count reset");
    expect(messages[0]).toContain("Reset the count to zero?");
  });
});

test.describe("Locations", () => {
  test("loads, saves and keeps the choice", async ({ page }) => {
    await page.goto("/");
    await page.click("#openLocations");
    await expect(page.locator("#locationsDialog")).toBeVisible();
    await expect(page.locator("#locJP")).toBeChecked();
    await expect(page.locator("#locRemote")).not.toBeChecked();
    await page.uncheck("#locJP");
    await page.check("#locRemote");
    await page.click("#locationsSave");
    await expect(page.locator("#locationsMsg")).toHaveText("Saved.");
    await page.click("#locationsClose");
    await expect(page.locator("#locationsDialog")).toBeHidden();
    await page.reload();
    await page.click("#openLocations");
    await expect(page.locator("#locJP")).not.toBeChecked();
    await expect(page.locator("#locRemote")).toBeChecked();
  });
});

test.describe("Sources", () => {
  test("lists sources and careers pages by kind", async ({ page }) => {
    await openMore(page, "#moreSources");
    await expect(page.locator("#nSources")).toHaveText("3");
    await expect(page.locator("#srcGroups h2")).toHaveText(["Company career sites", "Job boards"]);
    await expect(page.locator("#srcGroups")).toContainText("3 postings found");
    await expect(page.locator("#srcGroups")).toContainText("1 posting found");
    await expect(page.locator("#srcGroups")).toContainText("Works well");
    await expect(page.locator("#careersPagesPanel")).toBeVisible();
    await expect(page.locator("#careersPagesList")).toContainText("Careerco");
    await expect(page.locator("#careersPagesList")).toContainText("Careers page not working");
    await expect(page.locator("#careersPagesList")).toContainText("via Browser Run");
    await expect(page.locator("#careersPagesList")).toContainText("timeout");
  });

  test("adds a site, then removes it with a second click", async ({ page }) => {
    await openMore(page, "#moreSources");
    await page.click("#addBtn");
    await expect(page.locator("#status")).toContainText("Enter a site name");
    await page.fill("#addName", "My board");
    await page.fill("#addUrl", "https://myboard.example/jobs");
    await page.click("#addBtn");
    await expect(page.locator("#status")).toHaveText("Added My board.");
    await expect(page.locator("#srcGroups")).toContainText("My board");

    const row = page.locator("#srcGroups .src").filter({ hasText: "My board" });
    await row.getByRole("button", { name: "Remove" }).click();
    await row.getByRole("button", { name: "Confirm remove" }).click();
    await expect(page.locator("#status")).toHaveText("Removed My board.");
    await expect(page.locator("#srcGroups")).not.toContainText("My board");
  });

  test("a remove confirmation lapses on its own", async ({ page }) => {
    await openMore(page, "#moreSources");
    const button = page.locator("#srcGroups .src").first().locator(".btn.del");
    await button.click();
    await expect(button).toHaveText("Confirm remove");
    await expect(button).toHaveText("Remove", { timeout: 6000 });
  });
});

test.describe("Analytics", () => {
  test("shows each section from the seeded runs", async ({ page }) => {
    await openMore(page, "#moreAnalytics");
    await expect(page.locator("#analyticsBody")).toBeVisible();
    await expect(page.locator("#usageSummary")).toContainText("Billing: subscription");
    await expect(page.locator("#usageByStep")).toContainText("discovery (sonnet): 1,500 tokens, 2 searches, $0.12");
    await expect(page.locator("#coverageList")).toContainText("Apple: 0 postings");
    await expect(page.locator("#coverageList")).toContainText("Borderline: 0 postings (no source yet)");
    await expect(page.locator("#sourceQualityList")).toContainText("Amazon Jobs");
    await expect(page.locator("#firstToFindList")).not.toBeEmpty();
    await expect(page.locator("#perTitleList")).toContainText("Unmatched");
    await expect(page.locator("#unconfirmedSummary")).toContainText("not yet confirmed");
    await expect(page.locator("#verificationList")).toContainText("feed");
    await expect(page.locator("#prunedList")).toContainText("deleted: 1");
    await expect(page.locator("#alertHistoryList")).toContainText("feed_down (warning): A feed is down");
    await expect(page.locator("#postingsPerSourceList")).toContainText("Feeds:");
    await expect(page.locator("#careersPagesPerRunList")).toContainText("Browser Run: 4 loaded");
    await expect(page.locator("#careersPagesPerRunList")).toContainText("backup: 2 loaded");
    await expect(page.locator("#recentRunsList")).toContainText("completed, 2 new");
    await expect(page.locator("#recentRunsList")).toContainText("Degraded");
    await expect(page.locator("#recentRunsList")).toContainText("(over 30)");
    await expect(page.locator("#recentRunsList")).toContainText("completed, 2 new, 5 min, started by Cloudflare");
    await expect(page.locator("#recentRunsList")).toContainText("started by the GitHub schedule");
  });
});

test.describe("Proposals", () => {
  test("approve and reject", async ({ page }) => {
    await openMore(page, "#moreProposals");
    const rows = page.locator("#proposalsList .row-item");
    await expect(rows).toHaveCount(2);
    await expect(rows.first()).toContainText("Company: add — Newco");
    await rows.first().getByRole("button", { name: "Approve" }).click();
    await expect(page.locator("#status")).toHaveText("Approved.");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("New source: New Board (https://new.example)");
    await expect(rows.first()).toContainText("Looks useful");
    await rows.first().getByRole("button", { name: "Reject" }).click();
    await expect(page.locator("#status")).toHaveText("Rejected.");
    await expect(page.locator("#proposalsEmpty")).toBeVisible();
  });
});

test.describe("Titles", () => {
  const rowsIn = (page) => page.locator("#titlesBody .title-row");

  test("edit, add, delete, and save as a pull request", async ({ page }) => {
    await page.goto("/");
    await page.click("#openTitles");
    await expect(page.locator("#titlesDialog")).toBeVisible();
    await expect(rowsIn(page).first()).toBeVisible();
    await expect(page.locator("#titlesUnsaved")).toHaveText("No unsaved changes.");
    await expect(page.locator("#titlesSave")).toBeDisabled();
    await expect(page.locator("#titlesPending")).toBeHidden();

    // Turn a title off.
    await rowsIn(page).first().locator("input[type=checkbox]").uncheck();
    await expect(page.locator("#titlesUnsaved")).toHaveText("1 unsaved change.");
    await expect(rowsIn(page).first()).toHaveClass(/off/);

    // Add to an existing group, then to a new one.
    await page.fill("#titlesNewTitle", "Growth Product Intern");
    await page.click("#titlesAddBtn");
    await expect(rowsIn(page).filter({ hasText: "Growth Product Intern" })).toHaveCount(1);
    await page.selectOption("#titlesNewGroup", "__new__");
    await expect(page.locator("#titlesNewGroupName")).toBeFocused();
    await page.fill("#titlesNewTitle", "Platform Intern");
    await page.fill("#titlesNewGroupName", "Platform");
    await page.press("#titlesNewGroupName", "Enter");
    await expect(rowsIn(page).filter({ hasText: "Platform Intern" })).toHaveCount(1);
    await expect(page.locator("#titlesUnsaved")).toHaveText("3 unsaved changes.");
    await page.fill("#titlesNewTitle", "Zzz Spare Intern");
    await page.press("#titlesNewTitle", "Enter");
    await expect(rowsIn(page).filter({ hasText: "Zzz Spare Intern" })).toHaveCount(1);

    // Delete one, with a chance to back out.
    const doomed = rowsIn(page).filter({ hasText: "Zzz Spare Intern" });
    await doomed.getByRole("button", { name: "Delete Zzz Spare Intern" }).click();
    await expect(doomed).toContainText("Delete 'Zzz Spare Intern'?");
    await doomed.getByRole("button", { name: "Cancel" }).click();
    await expect(doomed.locator("input[type=checkbox]")).toBeVisible();
    await doomed.getByRole("button", { name: "Delete Zzz Spare Intern" }).click();
    await doomed.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(rowsIn(page).filter({ hasText: "Zzz Spare Intern" })).toHaveCount(0);

    await page.click("#titlesSave");
    await expect(page.locator("#titlesMsg")).toHaveText("Saved. Review and merge the pull request to apply it.");
    await expect(page.locator("#titlesPendingLink")).toContainText("#12");
    await expect(page.locator("#titlesPendingLink")).toHaveAttribute("href", /pull\/12/);
    await expect(page.locator("#titlesUnsaved")).toHaveText("No unsaved changes.");

    // Reopening shows the pending pull request and the saved edits.
    await page.click("#titlesClose");
    await page.click("#openTitles");
    await expect(page.locator("#titlesPending")).toBeVisible();
    await expect(rowsIn(page).filter({ hasText: "Platform Intern" })).toHaveCount(1);
  });

  test("checks what you type", async ({ page }) => {
    await page.goto("/");
    await page.click("#openTitles");
    await expect(rowsIn(page).first()).toBeVisible();
    const msg = page.locator("#titlesAddMsg");
    await page.click("#titlesAddBtn");
    await expect(msg).toHaveText("Enter a title.");
    await page.fill("#titlesNewTitle", "Product Manager Intern");
    await page.click("#titlesAddBtn");
    await expect(msg).toContainText("is already in the list, under 'Product Manager Intern'.");
    await page.fill("#titlesNewTitle", "Marketing Intern");
    await page.click("#titlesAddBtn");
    await expect(msg).toHaveText("'Marketing Intern' contains the excluded word 'marketing'.");
    await page.fill("#titlesNewTitle", "x".repeat(130));
    await expect(page.locator("#titlesNewTitle")).toHaveValue("x".repeat(120)); // the box stops at 120
    await page.fill("#titlesNewTitle", "Brand New Intern");
    await page.selectOption("#titlesNewGroup", "__new__");
    await page.click("#titlesAddBtn");
    await expect(msg).toHaveText("Enter a group name.");
  });

  test("filter, and apply JSON", async ({ page }) => {
    await page.goto("/");
    await page.click("#openTitles");
    await expect(rowsIn(page).first()).toBeVisible(); // the rows load after the dialog opens; count only once they are there
    const all = await rowsIn(page).count();
    await page.fill("#titlesFilter", "Product Management");
    expect(await rowsIn(page).count()).toBeLessThan(all);
    await page.fill("#titlesFilter", "zzzzzz");
    await expect(page.locator("#titlesBody")).toContainText("No titles match that filter.");
    await page.fill("#titlesFilter", "");

    await page.locator("#titlesJsonDetails summary").click();
    await page.fill("#titlesJson", "{not json");
    await page.click("#titlesJsonApply");
    await expect(page.locator("#titlesMsg")).toContainText("That's not valid JSON.");
    await page.fill("#titlesJson", JSON.stringify({ version: 1, exclude_words: [], titles: [{ title: "Only One", group: "G", aliases: [], active: true }] }));
    await page.click("#titlesJsonApply");
    await expect(page.locator("#titlesMsg")).toContainText("Applied.");
    await expect(rowsIn(page)).toHaveCount(1);
  });

  test("closing with unsaved changes asks first", async ({ page }) => {
    await page.goto("/");
    await page.click("#openTitles");
    await rowsIn(page).first().locator("input[type=checkbox]").uncheck();
    const answers = [];
    page.on("dialog", async (dialog) => {
      answers.push(dialog.message());
      if (answers.length === 1) await dialog.dismiss(); else await dialog.accept();
    });
    await page.click("#titlesClose");
    await expect(page.locator("#titlesDialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect.poll(() => answers.length).toBeGreaterThan(1);
    await expect(page.locator("#titlesDialog")).toBeHidden();
    expect(answers[0]).toBe("Discard 1 unsaved title change(s)?");
  });

  test("a save the server refuses is reported", async ({ page }) => {
    await page.goto("/");
    await page.click("#openTitles");
    await page.locator("#titlesJsonDetails summary").click();
    await page.fill("#titlesJson", JSON.stringify({ titles: [] }));
    await page.click("#titlesJsonApply");
    await page.click("#titlesSave");
    await expect(page.locator("#titlesMsg")).toContainText("Couldn't save.");
    await expect(page.locator("#titlesSave")).toBeEnabled();
  });
});

test.describe("Companies", () => {
  const section = (page, name) => page.locator("#companiesDialog .row-item").filter({ hasText: name });

  async function open(page) {
    await page.goto("/");
    await page.click("#openCompanies");
    await expect(page.locator("#companiesDialog")).toBeVisible();
    await expect(section(page, "Apple")).toBeVisible();
  }

  test("sorts companies into review, active and removed", async ({ page }) => {
    await open(page);
    await expect(page.locator("#reviewList")).toContainText("Zed Corp");
    await expect(section(page, "Apple")).toContainText("Rank 1 · greenhouse");
    await expect(section(page, "Borderline")).toContainText("no source yet");
    await expect(section(page, "Brokenfeed")).toContainText("feed error: HTTP 500");
    await expect(section(page, "Careerco")).toContainText("careers link on file");
    await expect(page.locator("#removedList")).toBeHidden();
    await page.click("#toggleRemoved");
    await expect(page.locator("#removedList")).toContainText("Gone Inc");
    await expect(page.locator("#toggleRemoved")).toHaveText("Hide removed companies");
    await page.getByRole("button", { name: "Restore" }).click();
    await expect(page.locator("#activeList")).toContainText("Gone Inc");
    await page.click("#companiesClose");
    await expect(page.locator("#companiesDialog")).toBeHidden();
  });

  test("pin and remove from review, and remove from the list", async ({ page }) => {
    await open(page);
    await page.locator("#reviewList").getByRole("button", { name: "Pin" }).click();
    await expect(page.locator("#reviewSection")).toBeHidden();
    await expect(page.locator("#activeList")).toContainText("Zed Corp");
    await section(page, "Zed Corp").getByRole("button", { name: "Remove" }).click();
    await expect(page.locator("#activeList")).not.toContainText("Zed Corp");
    await expect(page.locator("#removedSection")).toBeVisible();
  });

  test("a review company can be removed straight away", async ({ page }) => {
    await open(page);
    await page.locator("#reviewList").getByRole("button", { name: "Remove" }).click();
    await expect(page.locator("#reviewSection")).toBeHidden();
  });

  test("Check now starts, polls and reports", async ({ page, request }) => {
    await open(page);
    const apple = section(page, "Apple");
    await apple.getByRole("button", { name: "Check now" }).click();
    await expect(apple.locator(".check-status")).toHaveText("Running…");
    await request.post(DASHBOARD + "/__test/complete-check", { data: { result: { new_count: 2 } } });
    await expect(apple.locator(".check-status")).toHaveText("Done, 2 new postings.", { timeout: 12000 });
  });

  test("Check now reports a failed check", async ({ page, request }) => {
    await open(page);
    const apple = section(page, "Apple");
    await apple.getByRole("button", { name: "Check now" }).click();
    await expect(apple.locator(".check-status")).toHaveText("Running…");
    const failed = await request.post(DASHBOARD + "/__test/complete-check", { data: { result: { error: "boom" }, status: "failed" } });
    expect(failed.ok()).toBeTruthy();
    await expect(apple.locator(".check-status")).toHaveText("Check failed: boom", { timeout: 12000 });
  });

  test("adds a company by name and address", async ({ page }) => {
    await open(page);
    await page.click("#companyAddBtn");
    await expect(page.locator("#companiesMsg")).toHaveText("Enter a company name.");
    await page.fill("#companyName", "Newco");
    await page.click("#companyAddBtn");
    await expect(page.locator("#companiesMsg")).toHaveText("Added Newco.");
    await expect(page.locator("#activeList")).toContainText("Newco");
    await page.fill("#companyName", "Withurl");
    await page.fill("#companyUrl", "https://withurl.example/careers");
    await page.click("#companyAddBtn");
    await expect(section(page, "Withurl")).toContainText("careers link on file");
  });

  test("a duplicate company is refused", async ({ page }) => {
    await open(page);
    await page.fill("#companyName", "Apple");
    await page.click("#companyAddBtn");
    await expect(page.locator("#companiesMsg")).toContainText("Couldn't add Apple.");
  });

  test("paste a list, preview it, and save the new ones", async ({ page }) => {
    await open(page);
    await page.click("#pastePreviewBtn");
    await expect(page.locator("#companiesMsg")).toHaveText("Paste at least one company.");
    await page.fill("#pasteText", "Apple\nStripe\nFigma");
    await page.click("#pastePreviewBtn");
    await expect(page.locator("#pastePreviewResult tbody tr")).toHaveCount(3);
    await expect(page.locator("#pastePreviewResult input[type=checkbox]")).toHaveCount(2);
    await page.locator("#pastePreviewResult input[type=checkbox]").first().uncheck();
    await page.getByRole("button", { name: "Save selected" }).click();
    await expect(page.locator("#companiesMsg")).toHaveText("Saved 1 company.");
    await expect(page.locator("#pasteText")).toHaveValue("");
    await expect(page.locator("#activeList")).toContainText("Figma");
  });

  test("saving with nothing ticked says so", async ({ page }) => {
    await open(page);
    await page.fill("#pasteText", "Stripe");
    await page.click("#pastePreviewBtn");
    await page.locator("#pastePreviewResult input[type=checkbox]").uncheck();
    await page.getByRole("button", { name: "Save selected" }).click();
    await expect(page.locator("#companiesMsg")).toHaveText("Nothing selected to save.");
  });
});
