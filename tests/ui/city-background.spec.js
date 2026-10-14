const { test, expect, resetData, DASHBOARD, PUBLIC } = require("./fixtures");

test.beforeEach(async ({ request }) => resetData(request, { public_site: true }));

const VIEWPORT = { width: 1280, height: 720 };

// Software WebGL in CI can be slow, so these tests look at the page as soon as the scene is up.
async function waitForScene(page) {
  await expect(page.locator("#cityBackground")).toHaveAttribute("data-city-mode", /animate|still/);
}

async function boxOf(page, selector) {
  return page.locator(selector).evaluate((el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height };
  });
}

// "rgb(1, 2, 3)", "rgba(1, 2, 3, 0.5)" or "color(srgb 0.1 0.2 0.3 / 0.94)" -> { r, g, b, a } in 0..255.
function parseColor(text) {
  const nums = text.match(/-?\d*\.?\d+/g).map(Number);
  if (text.startsWith("color(")) return { r: nums[0] * 255, g: nums[1] * 255, b: nums[2] * 255, a: nums.length > 3 ? nums[3] : 1 };
  return { r: nums[0], g: nums[1], b: nums[2], a: nums.length > 3 ? nums[3] : 1 };
}

function over(top, under) {
  const a = top.a;
  return { r: top.r * a + under.r * (1 - a), g: top.g * a + under.g * (1 - a), b: top.b * a + under.b * (1 - a), a: 1 };
}

function luminance(c) {
  const [r, g, b] = [c.r, c.g, c.b].map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

test.describe("desktop", () => {
  test.use({ viewport: VIEWPORT });

  test("the scene covers the viewport, stays fixed while the page scrolls, and sits behind content", async ({ page }) => {
    await page.goto(DASHBOARD + "/");
    await expect(page.locator("#list li.item").first()).toBeVisible();
    await waitForScene(page);
    await expect(page.locator("#cityBackground")).toHaveAttribute("data-city-mode", "animate");
    await expect(page.locator("#cityBackground canvas")).toHaveCount(1);

    const before = await boxOf(page, "#cityBackground");
    expect(before).toEqual({ x: 0, y: 0, width: VIEWPORT.width, height: VIEWPORT.height });
    const canvas = await boxOf(page, "#cityBackground canvas");
    expect(canvas.width).toBeGreaterThanOrEqual(VIEWPORT.width - 1);
    expect(canvas.height).toBeGreaterThanOrEqual(VIEWPORT.height - 1);

    // Make sure there is something to scroll, then scroll.
    await page.evaluate(() => { document.querySelector(".wrap").style.minHeight = "3000px"; window.scrollTo(0, 900); });
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(500);
    expect(await boxOf(page, "#cityBackground")).toEqual(before);

    const style = await page.locator("#cityBackground").evaluate((el) => {
      const s = getComputedStyle(el);
      return { position: s.position, zIndex: s.zIndex, pointerEvents: s.pointerEvents };
    });
    expect(style).toEqual({ position: "fixed", zIndex: "-1", pointerEvents: "none" });

    // Whatever is under the pointer at the middle of the page is content, never the scene.
    const topmost = await page.evaluate(() => {
      const el = document.elementFromPoint(window.innerWidth / 2, 120);
      return !!el && !el.closest("#cityBackground");
    });
    expect(topmost).toBe(true);
  });

  test("every header button, tab and posting action is still clickable over it", async ({ page }) => {
    await page.goto(DASHBOARD + "/");
    await expect(page.locator("#list li.item").first()).toBeVisible();
    await waitForScene(page);

    const targets = page.locator(".tools .ghost, #searchNowBtn, .tabs > .tab, #list li.item .btn");
    const count = await targets.count();
    expect(count).toBeGreaterThan(10);
    for (let i = 0; i < count; i++) {
      // A trial click runs every actionability check, including "nothing else receives the click".
      await targets.nth(i).click({ trial: true });
    }
    // And a real one still works.
    await page.click("#openTitles");
    await expect(page.locator("dialog[open]")).toHaveCount(1);
  });

  test("posting title and company text meets AA contrast against the card, whatever the scene shows", async ({ page }) => {
    await page.goto(DASHBOARD + "/");
    await expect(page.locator("#list li.item").first()).toBeVisible();
    await waitForScene(page);
    const sample = await page.evaluate(() => {
      const card = document.querySelector("#list li.item");
      const pick = (el) => getComputedStyle(el);
      return {
        card: pick(card).backgroundColor,
        company: pick(card.querySelector(".co")).color,
        title: pick(card.querySelector(".ti")).color,
        meta: pick(card.querySelector(".meta")).color,
      };
    });
    const cardColor = parseColor(sample.card);
    expect(cardColor.a).toBeGreaterThanOrEqual(0.9); // near-opaque
    // Worst case behind the card is the brightest thing the scene can show: white.
    const worst = over(cardColor, { r: 255, g: 255, b: 255, a: 1 });
    for (const key of ["company", "title", "meta"]) {
      expect(ratio(parseColor(sample[key]), worst)).toBeGreaterThanOrEqual(4.5);
    }
  });

  test("Delete is visually distinct from the orange accent", async ({ page }) => {
    await page.goto(DASHBOARD + "/");
    await expect(page.locator("#list li.item .btn.del").first()).toBeVisible();
    const colors = await page.evaluate(() => ({
      del: getComputedStyle(document.querySelector("#list .btn.del")).color,
      count: getComputedStyle(document.getElementById("nToday")).color,
    }));
    const del = parseColor(colors.del);
    const orange = parseColor(colors.count);
    expect(`${del.r},${del.g},${del.b}`).not.toBe(`${orange.r},${orange.g},${orange.b}`);
    expect(Math.abs(del.b - orange.b)).toBeGreaterThan(40);
    expect(Math.abs(del.g - orange.g)).toBeGreaterThan(40);
  });

  test("the app is dark and neon: page and card colors are the new palette", async ({ page }) => {
    await page.goto(DASHBOARD + "/");
    await expect(page.locator("#list li.item").first()).toBeVisible();
    const colors = await page.evaluate(() => ({
      body: getComputedStyle(document.body).backgroundColor,
      ink: getComputedStyle(document.body).color,
      accent: getComputedStyle(document.documentElement).getPropertyValue("--accent").trim().toUpperCase(),
    }));
    expect(colors.body).toBe("rgb(5, 6, 10)");
    expect(colors.ink).toBe("rgb(242, 246, 255)");
    expect(colors.accent).toBe("#22D3EE");
  });

  test("the background is present on the public board too", async ({ page }) => {
    await page.goto(PUBLIC + "/");
    await expect(page.locator("#list li.item").first()).toBeVisible();
    await waitForScene(page);
    await expect(page.locator("#cityBackground")).toHaveAttribute("data-city-mode", "animate");
    expect(await boxOf(page, "#cityBackground")).toEqual({ x: 0, y: 0, width: VIEWPORT.width, height: VIEWPORT.height });
    await expect(page.locator("#cityBackground canvas")).toHaveCount(1);
    await page.locator("#tabEarlier").click({ trial: true });
    await page.locator("#openFeedback").click({ trial: true });
  });

  test("desktop loads three.js from the site itself, as a static file", async ({ page }) => {
    const requested = [];
    page.on("request", (r) => requested.push(new URL(r.url())));
    await page.goto(DASHBOARD + "/");
    await waitForScene(page);
    const three = requested.filter((u) => /three/.test(u.pathname));
    expect(three.length).toBe(1);
    expect(three[0].origin).toBe(DASHBOARD);
  });

  test("prefers-reduced-motion shows the still image and loads no three.js", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    const requested = [];
    page.on("request", (r) => requested.push(new URL(r.url()).pathname));
    await page.goto(DASHBOARD + "/");
    await waitForScene(page);
    await expect(page.locator("#cityBackground")).toHaveAttribute("data-city-mode", "still");
    await expect(page.locator("#cityBackground")).toHaveAttribute("data-city-reason", "reduced-motion");
    await expect(page.locator("#cityBackground img")).toBeVisible();
    expect(requested.filter((p) => /three/.test(p))).toEqual([]);
  });

  test("the still image really loads", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(PUBLIC + "/");
    await waitForScene(page);
    const loaded = await page.locator("#cityBackground img").evaluate((img) => img.decode().then(() => img.naturalWidth));
    expect(loaded).toBeGreaterThan(300);
  });
});

test.describe("phone, 390px wide with a touch screen", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("shows the still image, keeps the layout, and has no horizontal scroll", async ({ page }) => {
    const requested = [];
    page.on("request", (r) => requested.push(new URL(r.url()).pathname));
    await page.goto(DASHBOARD + "/");
    await expect(page.locator("#list li.item").first()).toBeVisible();
    await waitForScene(page);

    await expect(page.locator("#cityBackground")).toHaveAttribute("data-city-mode", "still");
    await expect(page.locator("#cityBackground canvas")).toHaveCount(0);
    await expect(page.locator("#cityBackground img")).toBeVisible();
    expect(requested.filter((p) => /three/.test(p))).toEqual([]);

    const layout = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      card: document.querySelector("#list li.item").getBoundingClientRect().width,
      scrollY: window.scrollY,
    }));
    expect(layout.scrollWidth).toBeLessThanOrEqual(layout.innerWidth);
    expect(layout.card).toBe(390 - 24); // the 12px side padding the phone layout already had
    expect(await boxOf(page, "#cityBackground")).toEqual({ x: 0, y: 0, width: 390, height: 844 });
  });

  test("the public board shows the still image too", async ({ page }) => {
    await page.goto(PUBLIC + "/");
    await expect(page.locator("#list li.item").first()).toBeVisible();
    await waitForScene(page);
    await expect(page.locator("#cityBackground")).toHaveAttribute("data-city-mode", "still");
    const scroll = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
    expect(scroll[0]).toBeLessThanOrEqual(scroll[1]);
  });
});
