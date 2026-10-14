// Renders one frame of the night city (the same scene the animation uses) to the static images
// phones, reduced-motion visitors, and browsers without WebGL see.
//
//   node scripts/render-city-still.js
//
// Writes frontend/public/city-still-{dark,light}.{webp,jpg}, each under 80KB, sized for phone
// screens (780 x 920, the 390 x 460 header box at 2x). Colors come from the CSS tokens in
// frontend/style.css through the same code the animation uses, so a theme change plus a rerun
// of this script keeps the still in step. Run it again whenever the scene changes.
//
// Needs Chromium from Playwright (`npx playwright install chromium`). It renders with the
// software WebGL that headless Chromium ships, so it works on a machine with no GPU.
const fs = require("fs");
const path = require("path");
const { chromium } = require("@playwright/test");

const ROOT = path.resolve(__dirname, "..");
const PUBLIC_DIR = path.join(ROOT, "frontend", "public");
const WIDTH = 780;
const HEIGHT = 920;
const MAX_BYTES = 80 * 1000;
const TARGET_BYTES = 76 * 1000; // headroom under the limit
const FRAME_DISTANCE = 3.7; // how far into a block the frame is taken, so it is not exactly on a row line

async function renderTheme(browser, theme, sources) {
  const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT }, colorScheme: theme });
  await page.setContent("<!doctype html><html><head></head><body><canvas id=c></canvas></body></html>");
  await page.addStyleTag({ content: sources.css });
  const result = await page.evaluate(async (args) => {
    // Load the city module without starting the page behavior: giving it a `module` takes its export path.
    const mod = { exports: {} };
    new Function("module", args.city)(mod);
    const city = mod.exports;
    const blobUrl = URL.createObjectURL(new Blob([args.three], { type: "text/javascript" }));
    const THREE = await import(blobUrl);

    const canvas = document.getElementById("c");
    const colors = city.deriveColors(city.readTokens(getComputedStyle(document.documentElement)));
    const scene = city.createScene(THREE, {
      canvas, width: args.width, height: args.height, dpr: 1, colors, preserveDrawingBuffer: true,
    });
    scene.step(args.distance);
    scene.render();

    function encode(type) {
      // Highest quality that stays under the target.
      for (let q = 0.92; q >= 0.2; q -= 0.04) {
        const url = canvas.toDataURL(type, q);
        const b64 = url.slice(url.indexOf(",") + 1);
        if (Math.floor((b64.length * 3) / 4) <= args.target) return { b64, quality: q };
      }
      throw new Error("could not get " + type + " under the size target");
    }
    const out = { colors, webp: encode("image/webp"), jpg: encode("image/jpeg") };
    scene.dispose();
    return out;
  }, { city: sources.city, three: sources.three, width: WIDTH, height: HEIGHT, distance: FRAME_DISTANCE, target: TARGET_BYTES });
  await page.close();

  for (const [key, ext] of [["webp", "webp"], ["jpg", "jpg"]]) {
    const file = path.join(PUBLIC_DIR, `city-still-${theme}.${ext}`);
    const bytes = Buffer.from(result[key].b64, "base64");
    if (bytes.length > MAX_BYTES) throw new Error(`${file} is ${bytes.length} bytes, over the limit`);
    fs.writeFileSync(file, bytes);
    console.log(`${path.relative(ROOT, file)}: ${bytes.length} bytes (quality ${result[key].quality.toFixed(2)})`);
  }
}

async function main() {
  const sources = {
    css: fs.readFileSync(path.join(ROOT, "frontend", "style.css"), "utf8"),
    city: fs.readFileSync(path.join(PUBLIC_DIR, "city-background.js"), "utf8"),
    three: fs.readFileSync(path.join(PUBLIC_DIR, "vendor", "three.min.mjs"), "utf8"),
  };
  const browser = await chromium.launch({
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"],
  });
  try {
    for (const theme of ["dark", "light"]) await renderTheme(browser, theme, sources);
  } finally {
    await browser.close();
  }
}

main().catch((err) => { console.error(err); process.exit(1); });
