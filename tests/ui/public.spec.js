const { test, expect, resetData, DASHBOARD, PUBLIC } = require("./fixtures");

const PRIVATE_VALUES = ["PRIVATE-NOTE-TEXT", "PRIVATE-STATUS-NOTE"];

test.describe("public page, site on", () => {
  test.beforeEach(async ({ request }) => resetData(request, { public_site: true }));

  test("shows only the seeded open postings, with company, title, locations and link", async ({ page }) => {
    await page.goto(PUBLIC + "/");
    await expect(page.locator("#board")).toBeVisible();
    await expect(page.locator("#nToday")).toHaveText("2");
    await expect(page.locator("#nEarlier")).toHaveText("2");

    const today = page.locator("#list li.item");
    await expect(today).toHaveCount(2);
    const google = today.filter({ hasText: "Google" });
    await expect(google.locator("h3")).toHaveText("Google");
    await expect(google.locator(".ti")).toHaveText("APM Intern");
    await expect(google.locator(".meta")).toContainText("Mountain View, CA, New York, NY");
    await expect(google.locator("a")).toHaveAttribute("href", "https://google.example/apm");

    // Closed, outside, hidden, archived and unclear-location postings never appear.
    const text = await page.locator("#list").innerText();
    for (const name of ["Meta", "Spotify", "Netflix", "Oracle", "Unclear Co"]) expect(text).not.toContain(name);

    await page.click("#tabEarlier");
    const earlier = page.locator("#list li.item");
    await expect(earlier).toHaveCount(2);
    await expect(earlier.filter({ hasText: "Sony" }).locator(".ti")).toHaveText("Product Intern"); // the English title
    await expect(page.locator("#list")).not.toContainText("Unclear Co");
  });

  test("tabs and search work", async ({ page }) => {
    await page.goto(PUBLIC + "/");
    await expect(page.locator("#tabToday")).toHaveAttribute("aria-selected", "true");
    await page.click("#tabEarlier");
    await expect(page.locator("#tabEarlier")).toHaveAttribute("aria-selected", "true");
    await page.fill("#filter", "tokyo");
    await expect(page.locator("#list li.item")).toHaveCount(1);
    await expect(page.locator("#list h3")).toHaveText("Sony");
    await page.fill("#filter", "no such thing");
    await expect(page.locator("#list .empty")).toHaveText("No postings match that search");
    await page.fill("#filter", "");
    await page.click("#tabToday");
    await page.fill("#filter", "amazon");
    await expect(page.locator("#list li.item")).toHaveCount(1);
  });

  test("the page, its script and the API payload carry no private values", async ({ page, request }) => {
    await page.goto(PUBLIC + "/");
    await expect(page.locator("#list li.item").first()).toBeVisible();
    await page.click("#tabEarlier");
    const html = await page.content();
    for (const value of PRIVATE_VALUES) expect(html).not.toContain(value);
    expect(html).not.toContain("Mark applied");
    expect(html).not.toContain("aria-pressed");
    expect(html).not.toContain("Applied");

    const body = await (await request.get(PUBLIC + "/api/public/postings")).text();
    for (const value of PRIVATE_VALUES) expect(body).not.toContain(value);
    const keys = new Set(JSON.parse(body).postings.flatMap((p) => Object.keys(p)));
    for (const forbidden of ["note", "applied", "user_status", "status_note", "public_hidden", "source_name"]) {
      expect(keys.has(forbidden)).toBe(false);
    }
  });

  test("the public hostname cannot reach the dashboard or its API", async ({ request }) => {
    expect((await request.get(PUBLIC + "/api/postings?tab=today")).status()).toBe(404);
    expect((await request.get(PUBLIC + "/app.js")).status()).toBe(404);
    expect((await request.get(PUBLIC + "/__test/reset")).status()).toBe(404);
    expect((await request.get(DASHBOARD + "/api/postings?tab=today")).status()).toBe(200);
  });

  test("a posting list that fails to load shows the unavailable line", async ({ page }) => {
    await page.route("**/api/public/postings", (route) => route.fulfill({ status: 500, body: "{}" }));
    await page.goto(PUBLIC + "/");
    await expect(page.locator("#unavailable")).toHaveText("Not available right now.");
    await expect(page.locator("#board")).toBeHidden();
  });
});

test.describe("public page, unconfirmed postings", () => {
  test.beforeEach(async ({ request }) => resetData(request, { public_site: true, unconfirmed: true }));

  test("an unconfirmed posting shows with a Not yet confirmed tag, confirmed ones without", async ({ page }) => {
    await page.goto(PUBLIC + "/");
    await expect(page.locator("#nToday")).toHaveText("3");
    const nintendo = page.locator("#list li.item").filter({ hasText: "Nintendo" });
    await expect(nintendo.locator(".ti")).toHaveText("Product Strategy Intern");
    await expect(nintendo.locator(".badge")).toHaveText("Not yet confirmed");
    await expect(nintendo.locator("a")).toHaveAttribute("href", "https://nintendo.example/ps");
    await expect(page.locator("#list li.item").filter({ hasText: "Google" }).locator(".badge")).toHaveCount(0);
    await expect(page.locator("#list li.item").filter({ hasText: "Amazon" }).locator(".badge")).toHaveCount(0);

    // An unconfirmed posting with an unclear location still stays off the public page.
    await page.click("#tabEarlier");
    await expect(page.locator("#list")).not.toContainText("Unclear Co");
  });
});

test.describe("public page, site off", () => {
  test.beforeEach(async ({ request }) => resetData(request, { public_site: false }));

  test('shows "Not available right now." and nothing else', async ({ page }) => {
    await page.goto(PUBLIC + "/");
    await expect(page.locator("#unavailable")).toHaveText("Not available right now.");
    await expect(page.locator("#board")).toBeHidden();
    await expect(page.locator("#list li")).toHaveCount(0);
    const visible = await page.locator("body").innerText();
    expect(visible.trim()).toBe("Not available right now.");
  });
});

test.describe("public feedback form", () => {
  async function openAndType(page, message) {
    await page.goto(PUBLIC + "/");
    await page.click("#openFeedback");
    await expect(page.locator("#feedbackDialog")).toBeVisible();
    await expect(page.locator("#fbKind")).toHaveValue("suggestion");
    if (message) await page.fill("#fbMessage", message);
  }

  const feedbackFromDashboard = async (request) => (await (await request.get(DASHBOARD + "/api/feedback")).json()).feedback;

  test("submits and says thanks", async ({ page, request }) => {
    await resetData(request, { public_site: true });
    await openAndType(page, "Please add Stripe");
    await page.click("#fbSend");
    await expect(page.locator("#fbMsg")).toHaveText("Thanks, sent.");
    await expect(page.locator("#fbMessage")).toHaveValue("");
    const rows = await feedbackFromDashboard(request);
    expect(rows.filter((f) => f.message === "Please add Stripe")).toHaveLength(1);
    await page.click("#fbClose");
    await expect(page.locator("#feedbackDialog")).toBeHidden();
  });

  test("an empty message is refused before anything is sent", async ({ page, request }) => {
    await resetData(request, { public_site: true });
    await openAndType(page, "   ");
    const before = (await feedbackFromDashboard(request)).length;
    await page.click("#fbSend");
    await expect(page.locator("#fbMsg")).toHaveText("Write a message first.");
    expect((await feedbackFromDashboard(request)).length).toBe(before);
  });

  test("a report on a card names the posting and files it against that posting", async ({ page, request }) => {
    await resetData(request, { public_site: true });
    await page.goto(PUBLIC + "/");
    await page.locator("#list li.item").filter({ hasText: "Google" }).getByRole("button", { name: "Report" }).click();
    await expect(page.locator("#fbPosting")).toHaveText("About: Google, APM Intern");
    await expect(page.locator("#fbKind")).toHaveValue("dead_link");
    await page.fill("#fbMessage", "Link is dead");
    await page.click("#fbSend");
    await expect(page.locator("#fbMsg")).toHaveText("Thanks, sent.");
    const rows = await feedbackFromDashboard(request);
    const mine = rows.find((f) => f.message === "Link is dead");
    expect(mine.posting_id).toBe(2);
    expect(mine.kind).toBe("dead_link");
  });

  test("the honeypot is invisible to people, and a bot that fills it is thrown away without a sign", async ({ page, request }) => {
    await resetData(request, { public_site: true });
    await openAndType(page, "I am a bot");
    await expect(page.locator("#fbWebsite")).not.toBeInViewport();
    await expect(page.locator("#fbWebsite")).toHaveAttribute("tabindex", "-1");
    const before = (await feedbackFromDashboard(request)).length;
    await page.evaluate(() => { document.getElementById("fbWebsite").value = "http://spam.example"; });
    await page.click("#fbSend");
    // The bot gets the same friendly answer a person gets, and nothing is stored.
    await expect(page.locator("#fbMsg")).toHaveText("Thanks, sent.");
    expect((await feedbackFromDashboard(request)).length).toBe(before);
  });

  test('over the limit shows "Try again tomorrow."', async ({ page, request }) => {
    await resetData(request, { public_site: true, rate_limited: true });
    await openAndType(page, "One more");
    await page.click("#fbSend");
    await expect(page.locator("#fbMsg")).toHaveText("Try again tomorrow.");
    await expect(page.locator("#fbSend")).toBeEnabled();
  });

  test("a rejected or unreachable send says so", async ({ page, request }) => {
    await resetData(request, { public_site: true });
    await openAndType(page, "x");
    await page.route("**/api/public/feedback", (route) => route.fulfill({ status: 400, body: "{}" }));
    await page.click("#fbSend");
    await expect(page.locator("#fbMsg")).toHaveText("Couldn't send that. Please check the message and try again.");
    await page.unroute("**/api/public/feedback");
    await page.route("**/api/public/feedback", (route) => route.abort());
    await page.click("#fbSend");
    await expect(page.locator("#fbMsg")).toHaveText("Couldn't send that. Please try again.");
  });
});
