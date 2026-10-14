// Phase 8 palette: the theme lives in the CSS token block and nowhere else.
const fs = require("fs");
const path = require("path");

const FRONTEND = path.resolve(__dirname, "..");
const read = (file) => fs.readFileSync(path.join(FRONTEND, file), "utf8");
const css = read("style.css");

// -- a small CSS reader: enough to find the token block and individual rules --------------------

function tokenBlock() {
  const match = css.match(/:root\s*\{([^}]*)\}/);
  return match ? match[1] : "";
}

function tokens() {
  const out = {};
  tokenBlock().replace(/(--[\w-]+)\s*:\s*([^;]+);?/g, (all, name, value) => { out[name] = value.trim(); return all; });
  return out;
}

function rules() {
  const list = [];
  css.replace(/([^{}]+)\{([^{}]*)\}/g, (all, selector, body) => { list.push({ selector: selector.trim(), body }); return all; });
  return list;
}

const bodiesFor = (selector) => rules().filter((r) => r.selector === selector).map((r) => r.body).join(";");

// -- WCAG contrast ------------------------------------------------------------------------------

function luminance(hex) {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)));
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("palette tokens", () => {
  test("are the approved colors", () => {
    const t = tokens();
    expect(t["--bg"].toUpperCase()).toBe("#05060A");
    expect(t["--surface"].toUpperCase()).toBe("#0B0E14");
    expect(t["--ink"].toUpperCase()).toBe("#F2F6FF");
    expect(t["--muted"].toUpperCase()).toBe("#8A9AB8");
    expect(t["--accent"].toUpperCase()).toBe("#22D3EE");
    expect(t["--accent-deep"].toUpperCase()).toBe("#1E6BFF");
    expect(t["--hot"].toUpperCase()).toBe("#FF7A1A");
    expect(t["--danger"].toUpperCase()).toBe("#FF4D5E");
  });

  test("there is one token block, with no light or dark variants left to drift", () => {
    expect(css.match(/:root/g)).toHaveLength(1);
    expect(css).not.toMatch(/prefers-color-scheme|data-theme/);
  });

  test("no color is written anywhere outside the token block", () => {
    const outside = css.replace(tokenBlock(), "");
    expect(outside).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(outside).not.toMatch(/\b(rgb|rgba|hsl|hsla)\(/);
  });

  test("pages and scripts do not hard-code colors either", () => {
    for (const file of ["index.html", "public/index.html", "app.js", "public/public.js", "public/city/city-background.js"]) {
      const text = read(file);
      expect(`${file}: ${(text.match(/#[0-9a-fA-F]{6}\b/g) || []).join(",")}`).toBe(`${file}: `);
      expect(text).not.toMatch(/\brgba?\(/);
    }
  });
});

describe("WCAG AA contrast (4.5:1) for every text and control pairing", () => {
  // [text token, background token, where it is used]
  const pairs = [
    ["--ink", "--bg", "page text"],
    ["--ink", "--surface", "card, panel and dialog text"],
    ["--muted", "--bg", "muted text on the page"],
    ["--muted", "--surface", "muted text on a card"],
    ["--accent", "--bg", "links and focus rings"],
    ["--accent", "--surface", "links on a card"],
    ["--accent", "--accent-soft", "active tab, closing badge"],
    ["--ink", "--accent-soft", "notes"],
    ["--muted", "--accent-soft", "muted text on a note"],
    ["--on-accent", "--accent-deep", "filled button"],
    ["--hot", "--surface", "New today count"],
    ["--hot", "--hot-soft", "closing-soon flag"],
    ["--on-hot", "--hot", "Search now while searching"],
    ["--danger", "--surface", "Delete and errors"],
    ["--danger", "--bg", "errors on the page"],
    ["--on-danger", "--danger", "armed Delete"],
    ["--muted", "--line", "closed badge"],
    ["--ok", "--ok-soft", "pressed buttons and ranked badge"],
  ];
  test.each(pairs)("%s on %s (%s)", (fg, bg) => {
    const t = tokens();
    expect(t[fg]).toBeDefined();
    expect(t[bg]).toBeDefined();
    expect(contrast(t[fg], t[bg])).toBeGreaterThanOrEqual(4.5);
  });

  test("the neon blue border and focus ring are visible against the page (3:1)", () => {
    const t = tokens();
    expect(contrast(t["--accent"], t["--bg"])).toBeGreaterThanOrEqual(3);
    expect(contrast(t["--accent"], t["--surface"])).toBeGreaterThanOrEqual(3);
  });
});

describe("where the colors are used", () => {
  test("neon blue marks buttons, the active tab, links and focus rings", () => {
    expect(bodiesFor(".tab[aria-selected=\"true\"]")).toContain("var(--accent)");
    expect(bodiesFor(":focus-visible")).toContain("var(--accent)");
    expect(bodiesFor(".pending-link a")).toContain("var(--accent)");
    expect(bodiesFor(".btn.primary")).toContain("var(--accent-deep)");
    expect(bodiesFor(".ghost.accent")).toContain("var(--accent-deep)");
  });

  test("orange is used for the New today count, Search now while searching, and closing-soon flags", () => {
    expect(bodiesFor("#nToday")).toContain("var(--hot)");
    expect(bodiesFor("#searchNowBtn[aria-busy=\"true\"]")).toContain("var(--hot)");
    expect(bodiesFor(".badge.closing")).toContain("var(--hot)");
  });

  test("orange appears nowhere else", () => {
    const users = rules().filter((r) => r.body.includes("--hot") || r.body.includes("--on-hot"));
    const selectors = users.map((r) => r.selector);
    const allowed = ["#nToday", "#searchNowBtn[aria-busy=\"true\"]", ".badge.closing"];
    for (const selector of selectors) {
      expect(allowed.some((a) => selector.startsWith(a))).toBe(true);
    }
  });

  test("red stays on Delete and errors, never on an ordinary accent", () => {
    const users = rules().filter((r) => r.body.includes("var(--danger)")).map((r) => r.selector);
    const ordinary = [".tab", ".ghost", ".btn.primary", ".btn.add", ".tag", ".badge.closing", ".badge.review", ".brand", "a"];
    for (const selector of users) expect(ordinary).not.toContain(selector);
    expect(bodiesFor(".btn.del")).toContain("var(--danger)");
    expect(bodiesFor(".search-status.err")).toContain("var(--danger)");
  });

  test("Delete stays visibly distinct from the orange accent", () => {
    const t = tokens();
    expect(t["--danger"].toUpperCase()).not.toBe(t["--hot"].toUpperCase());
    const lum = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    const [dr, dg, db] = lum(t["--danger"]);
    const [or, og, ob] = lum(t["--hot"]);
    // Different hue: red and blue channels carry the difference between a pink-red and an orange.
    expect(Math.abs(db - ob)).toBeGreaterThan(40);
    expect(Math.abs(dg - og)).toBeGreaterThan(40);
    expect(dr).toBeGreaterThan(0);
  });
});

describe("the city background layer", () => {
  test("is fixed to the viewport, behind content, and ignores the pointer", () => {
    const body = bodiesFor("#cityBackground");
    expect(body).toMatch(/position:\s*fixed/);
    expect(body).toMatch(/inset:\s*0/);
    expect(body).toMatch(/z-index:\s*-1/);
    expect(body).toMatch(/pointer-events:\s*none/);
  });

  test("a scrim built from the page color sits over the scene", () => {
    const scrim = bodiesFor("#cityBackground::after");
    expect(scrim).toContain("linear-gradient");
    expect(scrim).toContain("var(--bg)");
  });

  test("cards, panels and dialogs are near-opaque", () => {
    for (const selector of [".item", ".panel", "dialog", ".tabs"]) {
      expect(bodiesFor(selector)).toMatch(/color-mix\(in srgb, var\(--surface\) (9\d)%/);
    }
  });

  test("both pages hold the background layer and load its script", () => {
    for (const file of ["index.html", "public/index.html"]) {
      const html = read(file);
      expect(html).toContain('id="cityBackground"');
      expect(html).toContain('src="/public/city/city-background.js"');
      expect(html).not.toMatch(/three(\.min)?\.js|unpkg|cdn\./);
    }
  });
});
