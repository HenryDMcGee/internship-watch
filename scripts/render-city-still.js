// Renders one frame of the city background and saves it as the still image phones (and
// reduced-motion, no-WebGL and slow devices) show: frontend/public/city/city-still.webp plus a JPEG fallback.
// Run it again whenever the scene or the palette in frontend/style.css changes:
//
//     npm run city:still
//
// It draws the real scene with the real module, in headless Chromium, so the picture cannot drift from
// the animation. Colors are read from the CSS tokens like they are in the browser.
const fs = require("fs");
const http = require("http");
const path = require("path");
const { chromium } = require("@playwright/test");

const FRONTEND = path.resolve(__dirname, "..", "frontend");
const OUT = path.join(FRONTEND, "public", "city");
const WIDTH = 720;   // sized for phone screens; the page scales it with object-fit: cover
const HEIGHT = 1280;
const MAX_BYTES = 78 * 1024; // the limit is 80KB; stay clear of it
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html" };

const HARNESS = `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/style.css"><body>
<script src="/public/city/city-background.js"></script>`;

function serve() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/harness.html") {
      res.setHeader("content-type", "text/html");
      return res.end(HARNESS);
    }
    const file = path.join(FRONTEND, path.normalize(url.pathname));
    if (!file.startsWith(FRONTEND + path.sep) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.statusCode = 404;
      return res.end("not found");
    }
    res.setHeader("content-type", TYPES[path.extname(file)] || "application/octet-stream");
    return res.end(fs.readFileSync(file));
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

// Highest quality that fits under the size limit.
async function encode(page, type) {
  for (let q = 0.9; q >= 0.3; q -= 0.05) {
    const url = await page.evaluate(([t, quality]) => window.__canvas.toDataURL(t, quality), [type, q]);
    if (!url.startsWith("data:" + type)) throw new Error("This browser cannot encode " + type);
    const bytes = Buffer.from(url.split(",")[1], "base64");
    if (bytes.length <= MAX_BYTES) return { bytes, quality: q };
  }
  throw new Error("Could not get " + type + " under " + MAX_BYTES + " bytes. Simplify the scene or shrink WIDTH and HEIGHT.");
}

async function main() {
  const server = await serve();
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  try {
    const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT } });
    page.on("pageerror", (e) => { throw e; });
    await page.goto("http://127.0.0.1:" + server.address().port + "/harness.html");
    await page.evaluate(async ([width, height]) => {
      const THREE = await import("/public/city/vendor/three.min.js");
      const canvas = document.createElement("canvas");
      const city = window.CityBackground;
      const view = city.createView(THREE, city.readTokens(window, document), { canvas, width, height, dpr: 1, preserveDrawingBuffer: true });
      view.render(city.STILL_TIME_S);
      window.__canvas = view.renderer.domElement;
    }, [WIDTH, HEIGHT]);

    for (const [type, file] of [["image/webp", "city-still.webp"], ["image/jpeg", "city-still.jpg"]]) {
      const { bytes, quality } = await encode(page, type);
      fs.writeFileSync(path.join(OUT, file), bytes);
      console.log(file + ": " + bytes.length + " bytes (quality " + quality.toFixed(2) + ")");
    }
  } finally {
    await browser.close();
    server.close();
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
