// The files the city background serves: a small still image for phones, and a vendored three.js
// that only the animation loads.
const fs = require("fs");
const path = require("path");

const CITY = path.resolve(__dirname, "..", "public", "city");
const MAX_STILL_BYTES = 80 * 1024;

describe("static image", () => {
  const webp = path.join(CITY, "city-still.webp");
  const jpg = path.join(CITY, "city-still.jpg");

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

  test("is sized for a phone, in portrait", () => {
    const bytes = fs.readFileSync(jpg);
    let i = 2;
    let size = null;
    while (i < bytes.length && !size) {
      const marker = bytes[i + 1];
      const length = bytes.readUInt16BE(i + 2);
      if (marker >= 0xc0 && marker <= 0xc3) size = { height: bytes.readUInt16BE(i + 5), width: bytes.readUInt16BE(i + 7) };
      i += 2 + length;
    }
    expect(size).not.toBeNull();
    expect(size.width).toBeLessThanOrEqual(1080);
    expect(size.width).toBeGreaterThanOrEqual(600);
    expect(size.height).toBeGreaterThan(size.width);
  });
});

describe("vendored three.js", () => {
  test("is a static file in frontend, kept small by shipping only the classes the city uses", () => {
    const file = path.join(CITY, "vendor", "three.min.js");
    expect(fs.existsSync(file)).toBe(true);
    expect(fs.statSync(file).size).toBeLessThan(1024 * 1024);
  });

  test("records its version and license", () => {
    expect(fs.readFileSync(path.join(CITY, "vendor", "README.md"), "utf8")).toMatch(/three@\d+\.\d+\.\d+.*MIT/s);
  });
});
