const path = require("path");
const { makeFakeThree } = require("./fake-three");

const MODULE = path.resolve(__dirname, "..", "public", "city", "city-background.js");
const city = require(MODULE);

const TOKENS = { "--bg": "#05060A", "--surface": "#0B0E14", "--accent": "#22D3EE", "--accent-deep": "#1E6BFF", "--hot": "#FF7A1A" };

// -- helpers ------------------------------------------------------------------------------

function setEnv(opts) {
  window.innerWidth = opts.width;
  window.devicePixelRatio = opts.dpr || 1;
  window.matchMedia = (query) => ({
    matches: query.includes("pointer: coarse") ? !!opts.coarse : query.includes("prefers-reduced-motion") ? !!opts.reduced : false,
  });
}

function setTokens(over) {
  const all = Object.assign({}, TOKENS, over || {});
  document.head.innerHTML = "<style>:root{" + Object.keys(all).map((k) => k + ":" + all[k]).join(";") + "}</style>";
}

function setWebGL(available) {
  HTMLCanvasElement.prototype.getContext = jest.fn(() => (available ? {} : null));
}

function setHidden(hidden) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => (hidden ? "hidden" : "visible") });
  document.dispatchEvent(new Event("visibilitychange"));
}

// A hand-cranked clock and animation-frame queue.
function makeLoop() {
  let nextId = 0;
  const pending = new Map();
  const loop = {
    t: 0,
    now: () => loop.t,
    raf: jest.fn((cb) => { nextId += 1; pending.set(nextId, cb); return nextId; }),
    caf: jest.fn((id) => { pending.delete(id); }),
    pending: () => pending.size,
    // One display frame, `ms` after the last one.
    frame(ms) {
      loop.t += ms;
      const callbacks = Array.from(pending.values());
      pending.clear();
      callbacks.forEach((cb) => cb(loop.t));
    },
    // Runs frames of `ms` until `totalMs` has passed.
    run(totalMs, ms) {
      for (let spent = 0; spent < totalMs; spent += ms) loop.frame(ms);
    },
  };
  return loop;
}

function boot(env, extra) {
  setEnv(env);
  const fake = makeFakeThree();
  const loop = makeLoop();
  const loadThree = jest.fn(async () => fake.THREE);
  const options = Object.assign({ loadThree, now: loop.now, raf: loop.raf, caf: loop.caf }, extra || {});
  return { fake, loop, loadThree, start: () => city.start(options) };
}

beforeEach(() => {
  document.body.innerHTML = '<div id="cityBackground" aria-hidden="true"></div>';
  setTokens();
  setWebGL(true);
  setHidden(false);
});

const container = () => document.getElementById("cityBackground");

// -- camera --------------------------------------------------------------------------------

describe("camera constants", () => {
  test("are the approved values, named at the top of the module", () => {
    expect(city.TILT_DEG).toBe(40);
    expect(city.FOV_DEG).toBe(45);
    expect(city.ORBIT_PERIOD_S).toBeGreaterThanOrEqual(180);
    expect(city.ORBIT_PERIOD_S).toBeLessThanOrEqual(240);
    expect(city.DRIFT_FRACTION).toBeGreaterThanOrEqual(0.02);
    expect(city.DRIFT_FRACTION).toBeLessThanOrEqual(0.06);
    expect(city.DRIFT_PERIOD_S).toBeGreaterThan(city.ORBIT_PERIOD_S);
    expect(city.MAX_DPR).toBe(2);
    expect(city.MAX_FPS).toBe(30);
    expect(city.PHONE_MAX_WIDTH).toBe(768);
  });
});

describe("cameraPose", () => {
  const horizontal = (p) => Math.hypot(p.position.x - p.target.x, p.position.z - p.target.z);
  const samples = [];
  for (let t = 0; t <= 2 * city.DRIFT_PERIOD_S; t += 1.7) samples.push(t);

  test("completes one orbit in the configured period", () => {
    const start = city.cameraPose(0);
    const end = city.cameraPose(city.ORBIT_PERIOD_S);
    expect(end.position.x).toBeCloseTo(start.position.x, 6);
    expect(end.position.z).toBeCloseTo(start.position.z, 6);

    // A quarter of the period is a quarter turn, so the period is not a multiple or a fraction of one orbit.
    const quarter = city.cameraPose(city.ORBIT_PERIOD_S / 4);
    const a0 = Math.atan2(start.position.z, start.position.x);
    const a1 = Math.atan2(quarter.position.z, quarter.position.x);
    const turn = Math.abs(((a1 - a0 + 3 * Math.PI) % (2 * Math.PI)) - Math.PI);
    expect(turn).toBeCloseTo(Math.PI / 2, 6);
  });

  test("keeps a constant distance from the center through the orbit", () => {
    const radius = horizontal(city.cameraPose(0));
    for (const t of samples) expect(horizontal(city.cameraPose(t))).toBeCloseTo(radius, 9);
  });

  test("height varies only within the drift range, and does drift", () => {
    const low = city.CAMERA_HEIGHT * (1 - city.DRIFT_FRACTION);
    const high = city.CAMERA_HEIGHT * (1 + city.DRIFT_FRACTION);
    const heights = samples.map((t) => city.cameraPose(t).position.y);
    for (const y of heights) {
      expect(y).toBeGreaterThanOrEqual(low - 1e-9);
      expect(y).toBeLessThanOrEqual(high + 1e-9);
    }
    expect(Math.max(...heights) - Math.min(...heights)).toBeGreaterThan(city.CAMERA_HEIGHT * city.DRIFT_FRACTION);
  });

  test("always looks at the same fixed center and is tilted down about 40 degrees", () => {
    for (const t of samples) {
      const pose = city.cameraPose(t);
      expect(pose.target).toEqual(city.cameraPose(0).target);
      const tilt = (Math.atan2(pose.position.y - pose.target.y, horizontal(pose)) * 180) / Math.PI;
      expect(tilt).toBeGreaterThan(city.TILT_DEG - 3);
      expect(tilt).toBeLessThan(city.TILT_DEG + 3);
    }
  });

  test("never moves toward or away from the city: no flythrough", () => {
    // Distance in the ground plane is identical at every sample, so there is no forward travel.
    const first = horizontal(city.cameraPose(0));
    const travel = samples.map((t) => Math.abs(horizontal(city.cameraPose(t)) - first));
    expect(Math.max(...travel)).toBeLessThan(1e-9);
  });
});

// -- choosing between the animation and the still image ------------------------------------

describe("chooseMode", () => {
  const base = { width: 1440, coarse: false, reducedMotion: false, webgl: true };
  test("desktop animates", () => expect(city.chooseMode(base)).toEqual({ mode: "animate", reason: "" }));
  test("a phone (narrow and coarse) gets the still image", () =>
    expect(city.chooseMode({ ...base, width: 390, coarse: true })).toEqual({ mode: "still", reason: "phone" }));
  test("a small desktop window (narrow, fine pointer) animates", () =>
    expect(city.chooseMode({ ...base, width: 500 }).mode).toBe("animate"));
  test("a tablet (wide, coarse) animates", () =>
    expect(city.chooseMode({ ...base, width: 820, coarse: true }).mode).toBe("animate"));
  test("767px and coarse is a phone, 768px is not", () => {
    expect(city.chooseMode({ ...base, width: 767, coarse: true }).mode).toBe("still");
    expect(city.chooseMode({ ...base, width: 768, coarse: true }).mode).toBe("animate");
  });
  test("reduced motion gets the still image on any device", () =>
    expect(city.chooseMode({ ...base, reducedMotion: true })).toEqual({ mode: "still", reason: "reduced-motion" }));
  test("no WebGL gets the still image", () =>
    expect(city.chooseMode({ ...base, webgl: false })).toEqual({ mode: "still", reason: "no-webgl" }));
});

describe("readEnvironment and hasWebGL", () => {
  test("reads width and media queries, never the user agent", () => {
    setEnv({ width: 390, coarse: true, reduced: true });
    expect(city.readEnvironment(window)).toEqual({ width: 390, coarse: true, reducedMotion: true });
  });
  test("treats a missing matchMedia as a fine pointer without reduced motion", () => {
    setEnv({ width: 1000 });
    delete window.matchMedia;
    expect(city.readEnvironment(window)).toEqual({ width: 1000, coarse: false, reducedMotion: false });
  });
  test("hasWebGL is false when the context is missing or creating it throws", () => {
    setWebGL(false);
    expect(city.hasWebGL(document)).toBe(false);
    HTMLCanvasElement.prototype.getContext = () => { throw new Error("blocked"); };
    expect(city.hasWebGL(document)).toBe(false);
    setWebGL(true);
    expect(city.hasWebGL(document)).toBe(true);
  });
});

// -- start: what loads, what shows --------------------------------------------------------

describe("start", () => {
  test("a narrow viewport with a coarse pointer shows the still image and never loads three.js", async () => {
    const t = boot({ width: 390, coarse: true });
    const ctl = await t.start();
    expect(ctl.mode).toBe("still");
    expect(t.loadThree).not.toHaveBeenCalled();
    expect(container().querySelector("canvas")).toBeNull();
    expect(t.loop.raf).not.toHaveBeenCalled();
    const img = container().querySelector("picture.city-still img");
    expect(img).not.toBeNull();
    expect(img.getAttribute("alt")).toBe("");
    expect(container().querySelector('source[type="image/webp"]').getAttribute("srcset")).toMatch(/city-still\.webp$/);
    expect(img.getAttribute("src")).toMatch(/city-still\.jpg$/);
    expect(container().dataset.cityMode).toBe("still");
    expect(container().dataset.cityReason).toBe("phone");
  });

  test("a desktop viewport renders the canvas and loads three.js", async () => {
    const t = boot({ width: 1440 });
    const ctl = await t.start();
    expect(ctl.mode).toBe("animate");
    expect(t.loadThree).toHaveBeenCalledTimes(1);
    expect(container().querySelector("canvas")).not.toBeNull();
    expect(container().querySelector("img")).toBeNull();
    expect(container().dataset.cityMode).toBe("animate");
  });

  test("a narrow viewport with a fine pointer (a small desktop window) still animates", async () => {
    const t = boot({ width: 500, coarse: false });
    const ctl = await t.start();
    expect(ctl.mode).toBe("animate");
    expect(t.loadThree).toHaveBeenCalled();
  });

  test("prefers-reduced-motion shows the still image on any device", async () => {
    const t = boot({ width: 1920, reduced: true });
    const ctl = await t.start();
    expect(ctl.mode).toBe("still");
    expect(t.loadThree).not.toHaveBeenCalled();
    expect(container().dataset.cityReason).toBe("reduced-motion");
    expect(container().querySelector("img")).not.toBeNull();
  });

  test("no WebGL support shows the still image without throwing", async () => {
    setWebGL(false);
    const t = boot({ width: 1440 });
    await expect(t.start()).resolves.toMatchObject({ mode: "still" });
    expect(t.loadThree).not.toHaveBeenCalled();
    expect(container().dataset.cityReason).toBe("no-webgl");
  });

  test("a failed three.js download shows the still image", async () => {
    const t = boot({ width: 1440 }, { loadThree: async () => { throw new Error("offline"); } });
    const ctl = await t.start();
    expect(ctl.mode).toBe("still");
    expect(container().dataset.cityReason).toBe("load-failed");
  });

  test("a renderer that cannot start shows the still image", async () => {
    const t = boot({ width: 1440 });
    t.fake.THREE.WebGLRenderer = class { constructor() { throw new Error("no context"); } };
    const ctl = await t.start();
    expect(ctl.mode).toBe("still");
    expect(container().dataset.cityReason).toBe("no-webgl");
  });

  test("does nothing when the page has no background container", async () => {
    document.body.innerHTML = "";
    const t = boot({ width: 1440 });
    expect(await t.start()).toBeNull();
    expect(t.loadThree).not.toHaveBeenCalled();
  });

  test("a lost WebGL context swaps to the still image and records why", async () => {
    const t = boot({ width: 1440 });
    const ctl = await t.start();
    const renderer = t.fake.log.renderers[0];
    renderer.domElement.dispatchEvent(new Event("webglcontextlost", { cancelable: true }));
    expect(ctl.mode).toBe("still");
    expect(ctl.reason).toBe("context-lost");
    expect(container().dataset.cityReason).toBe("context-lost");
    expect(renderer.disposed).toBe(true);
    expect(container().querySelector("img")).not.toBeNull();
  });
});

// -- rendering ----------------------------------------------------------------------------

describe("render loop", () => {
  test("uses a small number of draw calls, built from instanced meshes", async () => {
    const t = boot({ width: 1440 });
    await t.start();
    const instanced = t.fake.log.meshes.filter((m) => m instanceof t.fake.THREE.InstancedMesh);
    expect(instanced.length).toBeGreaterThanOrEqual(2);
    expect(instanced[0].count).toBeGreaterThan(100);
    expect(t.fake.log.meshes.length).toBeLessThanOrEqual(5);
  });

  test("caps the device pixel ratio at 2 and sizes the canvas to the viewport", async () => {
    const t = boot({ width: 1440, dpr: 3 });
    window.innerHeight = 900;
    await t.start();
    const renderer = t.fake.log.renderers[0];
    expect(renderer.pixelRatio).toBe(2);
    expect(renderer.size).toEqual([1440, 900]);
  });

  test("keeps a pixel ratio of 1 as it is, and resizes with the window", async () => {
    const t = boot({ width: 1440, dpr: 1 });
    window.innerHeight = 900;
    await t.start();
    const renderer = t.fake.log.renderers[0];
    expect(renderer.pixelRatio).toBe(1);
    window.innerWidth = 1000; window.innerHeight = 700;
    window.dispatchEvent(new Event("resize"));
    expect(renderer.size).toEqual([1000, 700]);
    expect(t.fake.log.renderers[0].domElement.parentNode).toBe(container());
  });

  test("renders no more than 30 frames a second on a 60 Hz display", async () => {
    const t = boot({ width: 1440 });
    await t.start();
    const renderer = t.fake.log.renderers[0];
    t.loop.run(1000, 1000 / 60);
    expect(renderer.renders.length).toBeGreaterThan(20);
    expect(renderer.renders.length).toBeLessThanOrEqual(31);
  });

  test("one orbit of the clock turns the camera once around the same center", async () => {
    const t = boot({ width: 1440 });
    await t.start();
    const renderer = t.fake.log.renderers[0];
    t.loop.run(city.ORBIT_PERIOD_S * 1000, 1000 / 60);
    let swept = 0;
    let previous = null;
    for (const r of renderer.renders) {
      const a = Math.atan2(r.z, r.x);
      if (previous !== null) swept += ((a - previous + 3 * Math.PI) % (2 * Math.PI)) - Math.PI;
      previous = a;
    }
    expect(Math.abs(swept) / (2 * Math.PI)).toBeGreaterThan(0.97);
    expect(Math.abs(swept) / (2 * Math.PI)).toBeLessThan(1.03);
    // No flythrough: the distance across the ground and the look-at point never change.
    const radius = Math.hypot(renderer.renders[0].x, renderer.renders[0].z);
    for (const r of renderer.renders) {
      expect(Math.hypot(r.x, r.z)).toBeCloseTo(radius, 6);
      expect(r.target).toEqual(renderer.renders[0].target);
    }
  });

  test("a hidden tab stops the loop and a visible one resumes it", async () => {
    const t = boot({ width: 1440 });
    await t.start();
    const renderer = t.fake.log.renderers[0];
    t.loop.run(500, 34);
    const before = renderer.renders.length;
    expect(t.loop.pending()).toBe(1);

    setHidden(true);
    expect(t.loop.pending()).toBe(0);
    t.loop.run(500, 34);
    expect(renderer.renders.length).toBe(before);

    setHidden(false);
    expect(t.loop.pending()).toBe(1);
    t.loop.run(500, 34);
    expect(renderer.renders.length).toBeGreaterThan(before);
  });

  test("a tab that is already hidden at start does not run until it is shown", async () => {
    setHidden(true);
    const t = boot({ width: 1440 });
    await t.start();
    expect(t.loop.pending()).toBe(0);
    setHidden(false);
    expect(t.loop.pending()).toBe(1);
  });

  test("a sustained low frame rate swaps to the still image, disposes everything, and records why", async () => {
    const t = boot({ width: 1440 });
    const ctl = await t.start();
    const renderer = t.fake.log.renderers[0];
    t.loop.run(6000, 50); // 20 frames a second
    expect(ctl.mode).toBe("still");
    expect(ctl.reason).toBe("low-fps");
    expect(container().dataset.cityMode).toBe("still");
    expect(container().dataset.cityReason).toBe("low-fps");
    expect(renderer.disposed).toBe(true);
    expect(renderer.lost).toBe(true);
    expect(t.fake.log.geometries.length).toBeGreaterThan(0);
    t.fake.log.geometries.forEach((g) => expect(g.disposed).toBe(true));
    expect(t.fake.log.materials.length).toBeGreaterThan(0);
    t.fake.log.materials.forEach((m) => expect(m.disposed).toBe(true));
    expect(container().querySelector("canvas")).toBeNull();
    expect(container().querySelector("img")).not.toBeNull();
    expect(t.loop.pending()).toBe(0);
  });

  test("it takes about three seconds of low frame rate, not one slow moment", async () => {
    const t = boot({ width: 1440 });
    const ctl = await t.start();
    t.loop.run(1000, 16.7); // warm-up at full speed
    t.loop.run(1500, 50);
    expect(ctl.mode).toBe("animate");
    t.loop.run(1000, 16.7); // recovers, so the slow count starts over
    t.loop.run(2000, 50);
    expect(ctl.mode).toBe("animate");
  });

  test("a healthy 60 Hz display never falls back", async () => {
    const t = boot({ width: 1440 });
    const ctl = await t.start();
    t.loop.run(20000, 1000 / 60);
    expect(ctl.mode).toBe("animate");
  });

  test("stop() ends the loop and frees the scene without showing the image", async () => {
    const t = boot({ width: 1440 });
    const ctl = await t.start();
    ctl.stop();
    expect(t.loop.pending()).toBe(0);
    expect(t.fake.log.renderers[0].disposed).toBe(true);
    expect(container().querySelector("canvas")).toBeNull();
  });
});

// -- colors come from the CSS tokens --------------------------------------------------------

describe("colors", () => {
  const colorValues = (fake) => fake.log.colors.map((c) => c.value);

  test("readTokens reads the CSS custom properties", () => {
    setTokens({ "--accent": "#123456", "--hot": "#654321" });
    const tokens = city.readTokens(window, document);
    expect(tokens.window).toBe("#123456");
    expect(tokens.orange).toBe("#654321");
    expect(tokens.building).toBe(TOKENS["--surface"]);
    expect(tokens.ground).toBe(TOKENS["--bg"]);
    expect(tokens.deep).toBe(TOKENS["--accent-deep"]);
  });

  test("falls back to the default palette when a token is missing", () => {
    document.head.innerHTML = "";
    const tokens = city.readTokens(window, document);
    expect(tokens).toEqual(city.DEFAULT_TOKENS);
  });

  test("changing a token changes the scene", async () => {
    setTokens({ "--accent": "#22D3EE" });
    const a = boot({ width: 1440 });
    await a.start();
    expect(colorValues(a.fake)).toContain("#22D3EE");
    expect(colorValues(a.fake)).not.toContain("#abcdef");

    setTokens({ "--accent": "#abcdef", "--hot": "#fedcba", "--surface": "#010203" });
    const b = boot({ width: 1440 });
    await b.start();
    expect(colorValues(b.fake)).toEqual(expect.arrayContaining(["#abcdef", "#fedcba", "#010203"]));
    expect(colorValues(b.fake)).not.toContain("#22D3EE");
  });

  test("fog is the deep blue mixed into the background, so the district fades into the page", async () => {
    const t = boot({ width: 1440 });
    await t.start();
    const fog = t.fake.log.colors.find((c) => c.mixedWith);
    expect(fog.value).toBe(TOKENS["--bg"]);
    expect(fog.mixedWith[0]).toBe(TOKENS["--accent-deep"]);
  });
});

// -- the city itself ------------------------------------------------------------------------

describe("buildCity", () => {
  test("is the same city every time, with window strips and a few orange accents", () => {
    const one = makeFakeThree();
    const two = makeFakeThree();
    const a = city.buildCity(one.THREE, city.DEFAULT_TOKENS);
    const b = city.buildCity(two.THREE, city.DEFAULT_TOKENS);
    expect(a.stats).toEqual(b.stats);
    expect(a.stats.buildings).toBeGreaterThan(200);
    expect(a.stats.strips).toBeGreaterThan(a.stats.buildings);
    expect(a.stats.orange).toBeGreaterThan(0);
    expect(a.stats.orange).toBeLessThan(a.stats.strips * 0.2);
  });

  test("dispose frees every geometry and material", () => {
    const fake = makeFakeThree();
    const built = city.buildCity(fake.THREE, city.DEFAULT_TOKENS);
    built.dispose();
    fake.log.geometries.forEach((g) => expect(g.disposed).toBe(true));
    fake.log.materials.forEach((m) => expect(m.disposed).toBe(true));
  });
});
