const { test, expect, resetData, DASHBOARD, PUBLIC } = require("./fixtures");

const card = (page, company) => page.locator("#list li.item").filter({ has: page.locator("h3", { hasText: company }) });

test.beforeEach(async ({ request }) => resetData(request));

test.describe("postings", () => {
  test("the three tabs show the right postings", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#headline")).toHaveText("4 new postings"); // the count includes the one outside your locations
    await expect(page.locator("#nToday")).toHaveText("4");
    await expect(page.locator("#nEarlier")).toHaveText("3");
    await expect(page.locator("#nArchived")).toHaveText("1");

    // New today: open postings in the user's locations. Closed and outside ones sit behind toggles.
    await expect(page.locator("#list h3")).toHaveText(["Amazon", "Google", "Netflix"]);

    await page.click("#tabEarlier");
    await expect(page.locator("#tabEarlier")).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#list h3")).toHaveText(["Microsoft", "Sony", "Unclear Co"]);

    await page.click("#tabArchived");
    await expect(page.locator("#list h3")).toHaveText(["Oracle"]);
    await expect(card(page, "Oracle")).toContainText("Archived");
  });

  test("closed and outside postings stay hidden until asked for", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#list")).not.toContainText("Meta");
    await page.getByRole("button", { name: "Show 1 closed posting" }).click();
    await expect(card(page, "Meta")).toContainText("Closed");
    await expect(card(page, "Meta")).toContainText("View posting");
    await page.getByRole("button", { name: "Hide closed postings" }).click();
    await expect(page.locator("#list")).not.toContainText("Meta");

    await page.getByRole("button", { name: "Show 1 posting outside your locations" }).click();
    await expect(card(page, "Spotify")).toContainText("Outside your locations");
    await page.getByRole("button", { name: "Hide postings outside your locations" }).click();
    await expect(page.locator("#list")).not.toContainText("Spotify");
  });

  test("search filters the cards", async ({ page }) => {
    await page.goto("/");
    await page.fill("#filter", "google");
    await expect(page.locator("#list li.item")).toHaveCount(1);
    await page.fill("#filter", "zzz nothing");
    await expect(page.locator("#list .empty strong")).toHaveText("No postings match that filter");
    await page.fill("#filter", "");
    await expect(page.locator("#list li.item")).toHaveCount(3);
    await page.click("#tabEarlier");
    await page.fill("#filter", "tokyo");
    await expect(page.locator("#list h3")).toHaveText(["Sony"]);
  });

  test("a card shows its private notes, tags and verification", async ({ page }) => {
    await page.goto("/");
    const amazon = card(page, "Amazon");
    await expect(amazon).toContainText("PRIVATE-NOTE-TEXT");
    await expect(amazon).toContainText("Verified via the company's feed");
    await expect(amazon.getByRole("button", { name: "Applied" })).toHaveAttribute("aria-pressed", "true");
    await expect(amazon.getByRole("button", { name: "Mark open" })).toHaveAttribute("aria-pressed", "true");
    await expect(card(page, "Netflix")).toContainText("Hidden from public");
    await page.click("#tabEarlier");
    await expect(card(page, "Unclear Co")).toContainText("Location unclear");
    await expect(card(page, "Unclear Co")).toContainText("Status not confirmed");
    await expect(card(page, "Unclear Co").locator("a")).toHaveCount(0); // its address isn't a web link
  });

  test("Mark applied persists after a reload", async ({ page }) => {
    await page.goto("/");
    const google = card(page, "Google");
    await google.getByRole("button", { name: "Mark applied" }).click();
    await expect(google.getByRole("button", { name: "Applied" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator("#subline")).toContainText("2 marked applied");
    await page.reload();
    await expect(card(page, "Google").getByRole("button", { name: "Applied" })).toBeVisible();
    await card(page, "Google").getByRole("button", { name: "Applied" }).click();
    await expect(card(page, "Google").getByRole("button", { name: "Mark applied" })).toBeVisible();
  });

  test("Mark open and Mark closed save the answer", async ({ page }) => {
    await page.goto("/");
    await card(page, "Google").getByRole("button", { name: "Mark closed" }).click();
    await expect(card(page, "Google")).toHaveCount(0); // closed postings leave the list
    await page.getByRole("button", { name: "Show 2 closed postings" }).click();
    await expect(card(page, "Google")).toContainText("Closed");
    await card(page, "Google").getByRole("button", { name: "Mark open" }).click();
    await expect(card(page, "Google")).not.toContainText("Closed");
  });

  test("Hide from public tags the card, and again shows it", async ({ page, request }) => {
    await resetData(request, { public_site: true });
    await page.goto("/");
    const google = card(page, "Google");
    await google.getByRole("button", { name: "Hide from public" }).click();
    await expect(card(page, "Google")).toContainText("Hidden from public");
    await expect(page.locator("#status")).toHaveText("Hidden from the public page. The public page updates within 5 minutes.");
    await expect(card(page, "Google").getByRole("button", { name: "Hide from public" })).toHaveAttribute("aria-pressed", "true");

    const payload = await (await request.get(PUBLIC + "/api/public/postings")).json();
    expect(payload.postings.map((p) => p.company_name)).not.toContain("Google");

    await card(page, "Google").getByRole("button", { name: "Hide from public" }).click();
    await expect(card(page, "Google")).not.toContainText("Hidden from public");
    await expect(page.locator("#status")).toContainText("Shown on the public page again");
  });

  test("Delete asks twice, archives the posting, and Restore brings it back", async ({ page }) => {
    await page.goto("/");
    const google = card(page, "Google");
    await google.getByRole("button", { name: "Delete" }).click();
    await expect(google.getByRole("button", { name: "Confirm delete" })).toBeVisible();
    await google.getByRole("button", { name: "Confirm delete" }).click();
    await expect(page.locator("#nArchived")).toHaveText("2");
    await page.click("#tabArchived");
    await card(page, "Google").getByRole("button", { name: "Restore" }).click();
    await expect(page.locator("#status")).toHaveText("");
    await page.click("#tabToday");
    await expect(card(page, "Google")).toBeVisible();
  });

  test("the Delete confirmation lapses on its own", async ({ page }) => {
    await page.goto("/");
    const del = card(page, "Google").getByRole("button", { name: "Delete" });
    await del.click();
    await expect(card(page, "Google").locator(".btn.del")).toHaveText("Confirm delete");
    await expect(card(page, "Google").locator(".btn.del")).toHaveText("Delete", { timeout: 6000 });
  });
});

test.describe("Search now", () => {
  test("starts a search, shows progress, and reports the result", async ({ page, request }) => {
    await page.goto("/");
    await page.click("#searchNowBtn");
    await expect(page.locator("#searchNowBtn")).toBeDisabled();
    await expect(page.locator("#searchStatus")).toHaveText("Searching…");
    const done = await request.post(DASHBOARD + "/__test/complete-search", { data: { result: { new_count: 2, sources_found: 1 } } });
    expect(done.ok()).toBeTruthy();
    await expect(page.locator("#searchStatus")).toHaveText("Done: 2 new postings, 1 source found.", { timeout: 12000 });
    await expect(page.locator("#searchNowLabel")).toHaveText("Done: 2 new");
    await expect(page.locator("#searchNowBtn")).toBeEnabled();
  });

  test("a second tab sees the running search and picks up when it ends", async ({ page, request }) => {
    await page.goto("/");
    await page.click("#searchNowBtn");
    await expect(page.locator("#searchStatus")).toHaveText("Searching…");
    await page.reload();
    await expect(page.locator("#searchStatus")).toHaveText("A search is already running");
    await expect(page.locator("#searchNowBtn")).toBeDisabled();
    await request.post(DASHBOARD + "/__test/complete-search", { data: { result: {} } });
    await expect(page.locator("#searchNowBtn")).toBeEnabled({ timeout: 15000 });
    await expect(page.locator("#searchStatus")).toHaveText("");
  });
});

test.describe("More menu", () => {
  test("opens with the keyboard and moves between views", async ({ page }) => {
    await page.goto("/");
    await page.click("#tabMore");
    await expect(page.locator("#moreMenu")).toBeVisible();
    await expect(page.locator("#moreSources")).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(page.locator("#moreAnalytics")).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("ArrowUp");
    await expect(page.locator("#moreFeedback")).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.locator("#moreMenu")).toBeHidden();
    await expect(page.locator("#tabMore")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("#moreMenu")).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(page.locator("#moreMenu")).toBeHidden();
    await page.click("#tabMore");
    await page.click("h1");
    await expect(page.locator("#moreMenu")).toBeHidden();
  });
});

test.describe("Feedback view", () => {
  test("lists a visitor submission as text; Mark read and Dismiss work", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#nFeedback")).toHaveText("2");
    await page.click("#tabMore");
    await page.click("#moreFeedback");
    const first = page.locator("#feedbackList .row-item").filter({ hasText: "Seeded visitor note" });
    await expect(first).toContainText("Dead link (new)");
    await expect(first).toContainText("<b>x</b>"); // stored text is shown as text, never as HTML
    await expect(first.locator("b")).toHaveCount(0);
    await expect(first).toContainText("Google, APM Intern");

    await first.getByRole("button", { name: "Mark read" }).click();
    await expect(page.locator("#nFeedback")).toHaveText("1");
    await expect(first).toContainText(", read");
    await expect(first).not.toContainText("(new)");

    await first.getByRole("button", { name: "Dismiss" }).click();
    await expect(first).toContainText(", dismissed");
    await expect(first.getByRole("button", { name: "Dismiss" })).toHaveCount(0);

    // The change was saved, not just redrawn.
    await page.reload();
    await page.click("#tabMore");
    await page.click("#moreFeedback");
    await expect(page.locator("#feedbackList .row-item").filter({ hasText: "Seeded visitor note" })).toContainText(", dismissed");
  });

  test("the posting link jumps to the card", async ({ page }) => {
    await page.goto("/");
    await page.click("#tabMore");
    await page.click("#moreFeedback");
    await page.locator("#feedbackList").getByRole("link", { name: "Google, APM Intern" }).click();
    await expect(page.locator("#tabToday")).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#filter")).toHaveValue("Google");
    await expect(page.locator("#list h3")).toHaveText(["Google"]);
  });

  test("Open as GitHub issue links to the issue", async ({ page }) => {
    await page.goto("/");
    await page.click("#tabMore");
    await page.click("#moreFeedback");
    const second = page.locator("#feedbackList .row-item").filter({ hasText: "Second note" });
    await second.getByRole("button", { name: "Open as GitHub issue" }).click();
    await expect(second.getByRole("link", { name: "GitHub issue" })).toHaveAttribute("href", "https://github.com/HenryDMcGee/internship-watch/issues/77");
    await expect(second.getByRole("button", { name: "Open as GitHub issue" })).toHaveCount(0);
  });

  test("a note submitted on the public page shows up here", async ({ page, browser, request }) => {
    await resetData(request, { public_site: true });
    const visitor = await browser.newPage();
    await visitor.goto(PUBLIC + "/");
    await visitor.click("#openFeedback");
    await visitor.fill("#fbMessage", "From a real visitor");
    await visitor.click("#fbSend");
    await expect(visitor.locator("#fbMsg")).toHaveText("Thanks, sent.");
    await visitor.close();
    await page.goto("/");
    await page.click("#tabMore");
    await page.click("#moreFeedback");
    await expect(page.locator("#feedbackList")).toContainText("From a real visitor");
  });
});
