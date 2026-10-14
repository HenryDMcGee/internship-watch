// The city fills the whole window, on both pages, at any size. It is fixed to the viewport, so the
// area beside the centered content column shows it too.
const { test, expect, resetData, DASHBOARD, PUBLIC } = require("./fixtures");

test.beforeEach(async ({ request }) => resetData(request, { public_site: true }));
test.use({ reducedMotion: "no-preference" });

const SIZES = [
  { width: 1920, height: 1080 },
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
];

const PAGES = [
  ["dashboard", DASHBOARD, "#dateLine"],
  ["public board", PUBLIC, "#board"],
];

async function settle(page) {
  await expect(page.locator("#cityBg")).toHaveAttribute("data-city-mode", /^(animated|still)$/);
}

async function expectFillsViewport(page) {
  const layer = page.locator("#cityBg canvas, #cityBg img").first();
  await expect(layer).toBeVisible();
  await expect.poll(async () => {
    const box = await layer.boundingBox();
    const vp = page.viewportSize();
    return [box.x, box.y, box.width, box.height].map(Math.round).join(",") + "|" + vp.width + "x" + vp.height;
  }).toBe((() => { const vp = page.viewportSize(); return "0,0," + vp.width + "," + vp.height + "|" + vp.width + "x" + vp.height; })());
  expect(await page.locator("#cityBg").evaluate((el) => getComputedStyle(el).pointerEvents)).toBe("none");
}

// Share of pixels, in a small patch at each corner, that differ from the flat page color.
async function cornerShares(page) {
  const vp = page.viewportSize();
  const size = 24;
  const spots = [[0, 0], [vp.width - size, 0], [0, vp.height - size], [vp.width - size, vp.height - size]];
  const hex = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--bg").trim());
  const shares = [];
  for (const [x, y] of spots) {
    const png = await page.screenshot({ clip: { x, y, width: size, height: size } });
    shares.push(await page.evaluate(async ({ b64, bgHex }) => {
      const img = new Image();
      img.src = "data:image/png;base64," + b64;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = img.width; c.height = img.height;
      const ctx = c.getContext("2d");
      ctx.drawImage(img, 0, 0);
      const d = ctx.getImageData(0, 0, c.width, c.height).data;
      const want = [1, 3, 5].map((i) => parseInt(bgHex.slice(i, i + 2), 16));
      let off = 0;
      for (let i = 0; i < d.length; i += 4) {
        if (Math.max(Math.abs(d[i] - want[0]), Math.abs(d[i + 1] - want[1]), Math.abs(d[i + 2] - want[2])) > 3) off++;
      }
      return off / (d.length / 4);
    }, { b64: png.toString("base64"), bgHex: hex }));
  }
  return shares;
}

for (const [name, origin, ready] of PAGES) {
  test.describe(name, () => {
    for (const size of SIZES) {
      test("the canvas matches the viewport at " + size.width + "x" + size.height, async ({ page }) => {
        await page.setViewportSize(size);
        await page.goto(origin + "/");
        await expect(page.locator(ready)).toBeAttached();
        await settle(page);
        await expectFillsViewport(page);
      });
    }

    test("a point near each screen corner is not the flat page color", async ({ page }) => {
      await page.setViewportSize({ width: 1920, height: 1080 });
      await page.goto(origin + "/");
      await expect(page.locator(ready)).toBeAttached();
      await settle(page);
      await expect.poll(async () => Math.min(...(await cornerShares(page))), { timeout: 8000 }).toBeGreaterThan(0.02);
    });

    test("after resizing the window the canvas still matches the viewport", async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto(origin + "/");
      await settle(page);
      await expectFillsViewport(page);
      await page.setViewportSize({ width: 1600, height: 900 });
      await expectFillsViewport(page);
      await page.setViewportSize({ width: 900, height: 1200 });
      await expectFillsViewport(page);
    });

    test("html holds the page color; body and the wrapper are transparent", async ({ page }) => {
      await page.goto(origin + "/");
      await settle(page);
      const colors = await page.evaluate(() => {
        const bg = (el) => getComputedStyle(el).backgroundColor;
        return { html: bg(document.documentElement), body: bg(document.body), wrap: bg(document.querySelector(".wrap")) };
      });
      expect(colors.html).not.toMatch(/rgba\(.*,\s*0\)|transparent/);
      expect(colors.body).toMatch(/rgba\(0, 0, 0, 0\)|transparent/);
      expect(colors.wrap).toMatch(/rgba\(0, 0, 0, 0\)|transparent/);
    });
  });
}

test("full-window screenshot at 1920x1080", async ({ page }, info) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(PUBLIC + "/");
  await settle(page);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: info.outputPath("city-1920x1080.png") });
});
