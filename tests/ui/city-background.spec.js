// Phase 8: the night-city background on the dashboard and the public board.
const { test, expect, resetData, DASHBOARD, PUBLIC } = require("./fixtures");

test.beforeEach(async ({ request }) => resetData(request, { public_site: true }));

// The background is either the animated canvas or the static image, depending on the browser's WebGL.
const BACKGROUND = "#cityBg canvas, #cityBg img";

async function settle(page) {
  await expect(page.locator("#cityBg")).toHaveAttribute("data-city-mode", /^(animated|still)$/);
}

for (const [name, origin, ready] of [
  ["dashboard", DASHBOARD, "#dateLine"],
  ["public board", PUBLIC, "#board"],
]) {
  test.describe(name + " on a desktop", () => {
    test("shows the background behind the content, and it ignores the pointer", async ({ page }) => {
      await page.goto(origin + "/");
      await expect(page.locator(ready)).toBeAttached();
      await settle(page);
      await expect(page.locator(BACKGROUND).first()).toBeAttached();
      const style = await page.locator("#cityBg").evaluate((el) => {
        const s = getComputedStyle(el);
        return { events: s.pointerEvents, z: s.zIndex, position: s.position };
      });
      expect(style.events).toBe("none");
      expect(Number(style.z)).toBeLessThan(0);
      expect(style.position).toBe("absolute");
    });

    test("is a child of the body, not of the content", async ({ page }) => {
      await page.goto(origin + "/");
      await settle(page);
      expect(await page.locator("#cityBg").evaluate((el) => el.parentElement === document.body)).toBe(true);
      expect(await page.locator("#cityBg .city-scrim").count()).toBe(1);
    });

    test("does not cover the page: no horizontal scroll", async ({ page }) => {
      await page.goto(origin + "/");
      await settle(page);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    });
  });
}

test.describe("animation", () => {
  // playwright.config.js sets reducedMotion to "reduce" for every spec, so the animation tests turn it back on.
  test.use({ reducedMotion: "no-preference" });

  test("loads three.js from the site and paints the canvas, when the browser has WebGL", async ({ page }) => {
    const requested = [];
    page.on("request", (r) => requested.push(r.url()));
    await page.goto(DASHBOARD + "/");
    await settle(page);
    const webgl = await page.evaluate(() => !!document.createElement("canvas").getContext("webgl"));
    test.skip(!webgl, "this browser has no WebGL, so the static image is the right answer");
    // A slow software renderer may swap to the image after 3 seconds; the first look is the animation.
    const mode = await page.locator("#cityBg").getAttribute("data-city-mode");
    if (mode === "animated") {
      expect(requested.some((u) => u.endsWith("/public/vendor/three.min.mjs"))).toBe(true);
      const box = await page.locator("#cityBg canvas").boundingBox();
      expect(box.width).toBeGreaterThan(300);
      expect(box.height).toBeGreaterThan(100);
    } else {
      expect(await page.locator("#cityBg").getAttribute("data-city-reason")).toBe("low-fps");
    }
  });

  test("swaps to the static image when the frame rate stays low", async ({ page }) => {
    await page.addInitScript(() => {
      // Slow the display down to about 10 frames a second.
      const raf = window.requestAnimationFrame.bind(window);
      window.requestAnimationFrame = (fn) => raf(() => setTimeout(() => fn(performance.now()), 100));
    });
    await page.goto(DASHBOARD + "/");
    await settle(page);
    const webgl = await page.evaluate(() => !!document.createElement("canvas").getContext("webgl"));
    test.skip(!webgl, "this browser has no WebGL");
    await expect(page.locator("#cityBg")).toHaveAttribute("data-city-reason", "low-fps", { timeout: 10000 });
    await expect(page.locator("#cityBg canvas")).toHaveCount(0);
    await expect(page.locator("#cityBg img")).toBeAttached();
  });
});

test.describe("dashboard, with the background on", () => {
  test("header buttons and posting actions still work", async ({ page }) => {
    await page.goto(DASHBOARD + "/");
    await settle(page);
    await page.click("#openBudgets");
    await expect(page.locator("#budgetsDialog")).toBeVisible();
    await page.keyboard.press("Escape");

    const google = page.locator(".item", { hasText: "Google" });
    await google.getByRole("button", { name: "Mark applied" }).click();
    await expect(google.getByRole("button", { name: "Applied" })).toHaveAttribute("aria-pressed", "true");
  });

  test("the header buttons are the top element at their own position", async ({ page }) => {
    await page.goto(DASHBOARD + "/");
    await settle(page);
    const onTop = await page.locator("#openBudgets").evaluate((el) => {
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return hit === el || el.contains(hit);
    });
    expect(onTop).toBe(true);
  });

  test("postings stay clickable", async ({ page }) => {
    await page.goto(DASHBOARD + "/");
    await settle(page);
    const link = page.locator(".item a").first();
    await expect(link).toBeVisible();
    const onTop = await link.evaluate((el) => {
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return hit === el || el.contains(hit);
    });
    expect(onTop).toBe(true);
  });
});

test.describe("public board, with the background on", () => {
  test("tabs and search still work", async ({ page }) => {
    await page.goto(PUBLIC + "/");
    await settle(page);
    await page.click("#tabEarlier");
    await expect(page.locator("#tabEarlier")).toHaveAttribute("aria-selected", "true");
    await page.fill("#filter", "tokyo");
    await expect(page.locator("#list li.item")).toHaveCount(1);
  });

  test("the background files are served on the public hostname", async ({ request }) => {
    for (const path of ["/public/city-background.js", "/public/city-still-dark.jpg", "/public/city-still-dark.webp",
      "/public/city-still-light.jpg", "/public/city-still-light.webp", "/public/vendor/three.min.mjs"]) {
      const res = await request.get(PUBLIC + path);
      expect(res.status(), path).toBe(200);
    }
  });
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("shows the static image and loads no three.js", async ({ page }) => {
    const requested = [];
    page.on("request", (r) => requested.push(r.url()));
    await page.goto(DASHBOARD + "/");
    await expect(page.locator("#cityBg")).toHaveAttribute("data-city-mode", "still");
    await expect(page.locator("#cityBg")).toHaveAttribute("data-city-reason", "reduced-motion");
    await expect(page.locator("#cityBg img")).toBeAttached();
    await expect.poll(() => page.locator("#cityBg img").evaluate((i) => i.complete && i.naturalWidth > 0)).toBe(true);
    expect(requested.filter((u) => u.includes("three"))).toEqual([]);
  });
});

test.describe("at 390px wide on a touch screen", () => {
  // "no-preference" so the phone check is what picks the image, not reduced motion.
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, reducedMotion: "no-preference" });

  for (const [name, origin, ready] of [
    ["dashboard", DASHBOARD, "#dateLine"],
    ["public board", PUBLIC, "#board"],
  ]) {
    test("the " + name + " shows the static image, has no horizontal scroll, and downloads no three.js", async ({ page }) => {
      const requested = [];
      page.on("request", (r) => requested.push(r.url()));
      await page.goto(origin + "/");
      await expect(page.locator(ready)).toBeAttached();
      await expect(page.locator("#cityBg")).toHaveAttribute("data-city-mode", "still");
      await expect(page.locator("#cityBg")).toHaveAttribute("data-city-reason", "phone");
      await expect(page.locator("#cityBg canvas")).toHaveCount(0);
      await expect.poll(() => page.locator("#cityBg img").evaluate((i) => i.complete && i.naturalWidth > 0)).toBe(true);
      expect(requested.filter((u) => u.includes("three"))).toEqual([]);

      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    });
  }

  test("the layout is unchanged: the headline sits where it did without the background", async ({ page }) => {
    await page.goto(DASHBOARD + "/");
    await expect(page.locator("#cityBg")).toHaveAttribute("data-city-mode", "still");
    const withBg = await page.locator(".hero").boundingBox();
    await page.evaluate(() => document.getElementById("cityBg").remove());
    const without = await page.locator(".hero").boundingBox();
    expect(withBg).toEqual(without);
  });

  test("buttons still work over the image", async ({ page }) => {
    await page.goto(DASHBOARD + "/");
    await expect(page.locator("#cityBg")).toHaveAttribute("data-city-mode", "still");
    await page.tap("#openBudgets");
    await expect(page.locator("#budgetsDialog")).toBeVisible();
  });
});
