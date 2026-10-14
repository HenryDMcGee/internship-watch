// The city must be visible, not just present in the page. A canvas or image that exists but is
// covered by another layer, or that renders nothing, fails here. Issue #88.
const { test, expect, resetData, DASHBOARD, PUBLIC } = require("./fixtures");

test.beforeEach(async ({ request }) => resetData(request, { public_site: true }));

const PAGES = [
  ["dashboard", DASHBOARD, "#dateLine"],
  ["public board", PUBLIC, "#board"],
];

// Screenshots the strip to the left of the content column, where only the background shows, and
// counts the pixels that are not the page color. Reads the screenshot, so it sees what a visitor
// sees: stacking order, the scrim, and the mask all count.
async function litPixels(page) {
  const bgColor = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--bg").trim());
  const png = await page.screenshot({ clip: { x: 0, y: 0, width: 240, height: 420 } });
  return page.evaluate(async ({ b64, hex }) => {
    const img = new Image();
    img.src = "data:image/png;base64," + b64;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width; c.height = img.height;
    const ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0);
    const data = ctx.getImageData(0, 0, c.width, c.height).data;
    const want = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    let lit = 0;
    for (let i = 0; i < data.length; i += 4) {
      if (Math.max(Math.abs(data[i] - want[0]), Math.abs(data[i + 1] - want[1]), Math.abs(data[i + 2] - want[2])) > 6) lit++;
    }
    return { lit, total: data.length / 4 };
  }, { b64: png.toString("base64"), hex: bgColor });
}

for (const [name, origin, ready] of PAGES) {
  test.describe(name + ", animation allowed", () => {
    test.use({ reducedMotion: "no-preference" });

    test("shows a canvas (or, if the browser can't animate, the image) with a size, and it paints", async ({ page }) => {
      await page.goto(origin + "/");
      await expect(page.locator(ready)).toBeAttached();
      await expect(page.locator("#cityBg")).toHaveAttribute("data-city-mode", /^(animated|still)$/);

      const layer = page.locator("#cityBg canvas, #cityBg img").first();
      await expect(layer).toBeVisible();
      const box = await layer.boundingBox();
      expect(box.width).toBeGreaterThan(300);
      expect(box.height).toBeGreaterThan(100);

      const { lit, total } = await litPixels(page);
      expect(lit / total, "share of pixels that are not the page color").toBeGreaterThan(0.02);
    });

    test("nothing sits over the city: no other layer is painted above it at the page edge", async ({ page }) => {
      await page.goto(origin + "/");
      await expect(page.locator("#cityBg")).toHaveAttribute("data-city-mode", /^(animated|still)$/);
      // Fixed or absolute elements, other than the city itself, that cover the left strip and are opaque.
      const covering = await page.evaluate(() => {
        const out = [];
        for (const el of document.querySelectorAll("body *")) {
          if (el.closest("#cityBg")) continue;
          const s = getComputedStyle(el);
          if (s.position !== "fixed" && s.position !== "absolute") continue;
          const r = el.getBoundingClientRect();
          const opaque = !/rgba?\(\s*\d+,\s*\d+,\s*\d+,\s*0\s*\)|transparent/.test(s.backgroundColor) && s.backgroundColor !== "";
          if (r.left <= 0 && r.top <= 0 && r.width >= innerWidth - 1 && r.height >= 400 && opaque && s.display !== "none") {
            out.push(el.tagName + "#" + el.id + "." + el.className);
          }
        }
        return out;
      });
      expect(covering).toEqual([]);
    });
  });

  test.describe(name + ", static image", () => {
    test.use({ reducedMotion: "reduce" });

    test("the image paints too", async ({ page }) => {
      await page.goto(origin + "/");
      await expect(page.locator("#cityBg")).toHaveAttribute("data-city-mode", "still");
      await expect.poll(() => page.locator("#cityBg img").evaluate((i) => i.complete && i.naturalWidth > 0)).toBe(true);
      const { lit, total } = await litPixels(page);
      expect(lit / total).toBeGreaterThan(0.02);
    });
  });

  test.describe(name + ", when three.js fails", () => {
    test.use({ reducedMotion: "no-preference" });

    test("logs a console error naming the step and shows the image instead of nothing", async ({ page }) => {
      const errors = [];
      page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
      await page.route("**/public/vendor/three.min.mjs", (route) => route.abort());
      await page.goto(origin + "/");
      await expect(page.locator("#cityBg")).toHaveAttribute("data-city-mode", "still");
      await expect(page.locator("#cityBg")).toHaveAttribute("data-city-reason", "load-failed");
      await expect.poll(() => page.locator("#cityBg img").evaluate((i) => i.complete && i.naturalWidth > 0)).toBe(true);
      expect(errors.some((e) => /city background.*load three\.js/i.test(e)), errors.join("\n")).toBe(true);
      const { lit, total } = await litPixels(page);
      expect(lit / total).toBeGreaterThan(0.02);
    });
  });
}

// The Worker uploads everything under frontend/ as static assets. These are the paths the page asks
// for, requested the way the browser does, on both hostnames. (Production sits behind Cloudflare Access,
// so this runs against the same app and assets locally and in CI.)
test.describe("the background files are served", () => {
  const ASSETS = [
    ["/public/city-background.js", /javascript/],
    ["/public/vendor/three.min.mjs", /javascript/],
    ["/public/city-still-dark.jpg", /image\/jpeg/],
    ["/public/city-still-dark.webp", /image\/webp/],
    ["/public/city-still-light.jpg", /image\/jpeg/],
    ["/public/city-still-light.webp", /image\/webp/],
  ];
  for (const [name, origin] of [["dashboard", DASHBOARD], ["public board", PUBLIC]]) {
    test("on the " + name + " the module, three.js and the images return 200", async ({ request }) => {
      for (const [path, type] of ASSETS) {
        const res = await request.get(origin + path);
        expect(res.status(), path).toBe(200);
        expect(res.headers()["content-type"] || "", path).toMatch(type);
        expect((await res.body()).length, path).toBeGreaterThan(1000);
      }
    });
  }
});
