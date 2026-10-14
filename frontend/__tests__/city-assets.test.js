// The files the city background serves: a small still image for phones, and a vendored three.js
// that only the animation loads. The still is always the dark one, because the site has one palette.
const fs = require("fs");
const path = require("path");

const PUBLIC = path.resolve(__dirname, "..", "public");
const MAX_STILL_BYTES = 80 * 1024;

describe("static image", () => {
  const webp = path.join(PUBLIC, "city-still-dark.webp");
  const jpg = path.join(PUBLIC, "city-still-dark.jpg");

  test("WebP exists, is a WebP file, and is under 80KB", () => {
    const bytes = fs.readFileSync(webp);
    expect(bytes.subarray(0, 4).toString("latin1")).toBe("RIFF");
    expect(bytes.subarray(8, 12).toString("latin1")).toBe("WEBP");
    expect(bytes.length).toBeLessThan(MAX_STILL_BYTES);
  });

  test("JPEG fallback exists, is a JPEG file, and is under 80KB", () => {
    const bytes = fs.readFileSync(jpg);
    expect(Array.from(bytes.subarray(0, 3))).toEqual([0xff, 0xd8, 0xff]);
    expect(bytes.length).toBeLessThan(MAX_STILL_BYTES);
  });
});

describe("vendored three.js", () => {
  test("is a static file in frontend, kept small by shipping only the classes the city uses", () => {
    const file = path.join(PUBLIC, "vendor", "three.min.mjs");
    expect(fs.existsSync(file)).toBe(true);
    expect(fs.statSync(file).size).toBeLessThan(1024 * 1024);
  });

  test("records its license", () => {
    expect(fs.existsSync(path.join(PUBLIC, "vendor", "LICENSE-three.txt"))).toBe(true);
  });
});

describe("one copy of the background", () => {
  test("the old frontend/public/city/ copy is gone", () => {
    expect(fs.existsSync(path.join(PUBLIC, "city"))).toBe(false);
  });

  test("nothing in the repo's scripts or docs points at it", () => {
    const root = path.resolve(__dirname, "..", "..");
    for (const file of ["package.json", "README.md", "docs/deploy.md", "scripts/three-entry.js"]) {
      const text = fs.existsSync(path.join(root, file)) ? fs.readFileSync(path.join(root, file), "utf8") : "";
      expect(`${file}: ${/public\/city\/|build:three/.test(text)}`).toBe(`${file}: false`);
    }
  });
});
