// Shared test setup: blocks Google Fonts, collects Chromium's V8 coverage for the frontend scripts,
// and converts it to Istanbul format for nyc (see package.json's coverage:ui script).
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const base = require("@playwright/test");
const v8toIstanbul = require("v8-to-istanbul");
const libCoverage = require("istanbul-lib-coverage");

const ROOT = path.resolve(__dirname, "..", "..");
const FRONTEND = path.join(ROOT, "frontend");
const NYC_OUTPUT = path.join(ROOT, ".nyc_output");
const DASHBOARD = "http://127.0.0.1:8787";
const PUBLIC = "http://127.0.0.1:8788";

// "http://127.0.0.1:8787/app.js" -> frontend/app.js. Anything else (inline scripts, other files) is skipped.
function frontendFileFor(url) {
  let parsed;
  try { parsed = new URL(url); } catch (e) { return null; }
  if (parsed.origin !== DASHBOARD && parsed.origin !== PUBLIC) return null;
  const file = path.join(FRONTEND, parsed.pathname.replace(/^\//, ""));
  return file.endsWith(".js") && file.startsWith(FRONTEND + path.sep) && fs.existsSync(file) ? file : null;
}

async function saveCoverage(entries) {
  const map = libCoverage.createCoverageMap({});
  for (const entry of entries) {
    const file = frontendFileFor(entry.url);
    if (!file) continue;
    const converter = v8toIstanbul(file, 0, { source: entry.source });
    await converter.load();
    converter.applyCoverage(entry.functions);
    map.merge(converter.toIstanbul());
  }
  if (!Object.keys(map.data).length) return;
  fs.mkdirSync(NYC_OUTPUT, { recursive: true });
  fs.writeFileSync(path.join(NYC_OUTPUT, crypto.randomUUID() + ".json"), JSON.stringify(map.toJSON()));
}

const test = base.test.extend({
  page: async ({ page }, use) => {
    await page.route(/fonts\.(googleapis|gstatic)\.com/, (route) => route.abort());
    await page.coverage.startJSCoverage({ resetOnNavigation: false });
    await use(page);
    const entries = await page.coverage.stopJSCoverage();
    await saveCoverage(entries);
  },
});

// Puts the server's database back to the seeded starting point.
async function resetData(request, options) {
  const res = await request.post(DASHBOARD + "/__test/reset", { data: options || {} });
  base.expect(res.ok()).toBeTruthy();
}

module.exports = { test, expect: base.expect, resetData, DASHBOARD, PUBLIC };
