// Phase 8: the night-city background. Written before frontend/public/city-background.js.
// The module takes three.js as an argument (loadThree), so these tests use a small fake of it
// and never download or run the real library.
const city = require("../public/city-background.js");

// ---- a fake of the parts of three.js the scene uses ------------------------------------

function fakeThree() {
  const made = { renderers: [], geometries: [], materials: [], meshes: [], cameras: [] };
  class Vec { constructor() { this.x = 0; this.y = 0; this.z = 0; } set(x, y, z) { this.x = x; this.y = y; this.z = z; return this; } }
  class Color {
    constructor(v) { this.v = v; }
    set(v) { this.v = v; return this; }
    setRGB(r, g, b) { this.v = [r, g, b]; return this; }
  }
  class Object3D {
    constructor() { this.position = new Vec(); this.scale = new Vec(); this.rotation = new Vec(); this.matrix = {}; this.children = []; }
    updateMatrix() {}
    add(child) { this.children.push(child); }
    lookAt() {}
  }
  class Scene extends Object3D { constructor() { super(); this.fog = null; this.background = null; } }
  class Fog { constructor(color, near, far) { this.color = new Color(color); this.near = near; this.far = far; } }
  class PerspectiveCamera extends Object3D {
    constructor(fov, aspect, near, far) { super(); this.fov = fov; this.aspect = aspect; this.near = near; this.far = far; made.cameras.push(this); }
    updateProjectionMatrix() { this.projected = (this.projected || 0) + 1; }
  }
  class BoxGeometry { constructor() { this.dispose = jest.fn(); made.geometries.push(this); } }
  class Material {
    constructor(opts) { this.color = new Color(opts && opts.color); this.dispose = jest.fn(); made.materials.push(this); }
  }
  class InstancedMesh extends Object3D {
    constructor(geometry, material, count) {
      super();
      this.geometry = geometry; this.material = material; this.count = count;
      this.instanceMatrix = { needsUpdate: false };
      this.matrices = new Array(count);
      made.meshes.push(this);
    }
    setMatrixAt(i, m) { this.matrices[i] = m; }
  }
  class Light extends Object3D { constructor(color, intensity) { super(); this.color = new Color(color); this.intensity = intensity; } }
  class WebGLRenderer {
    constructor(opts) {
      this.domElement = opts.canvas;
      this.opts = opts;
      this.pixelRatio = null; this.size = null; this.renders = 0;
      this.setClearColor = jest.fn();
      this.dispose = jest.fn();
      this.forceContextLoss = jest.fn();
      made.renderers.push(this);
    }
    setPixelRatio(r) { this.pixelRatio = r; }
    setSize(w, h, updateStyle) { this.size = [w, h, updateStyle]; }
    render() { this.renders += 1; }
  }
  return {
    made,
    THREE: {
      WebGLRenderer, Scene, Fog, Color, PerspectiveCamera, BoxGeometry, InstancedMesh, Object3D,
      MeshLambertMaterial: Material, MeshBasicMaterial: Material,
      DirectionalLight: Light, AmbientLight: Light,
    },
  };
}

// ---- a browser environment the tests can steer ----------------------------------------

function makeEnv(opts) {
  const o = Object.assign({ width: 1280, coarse: false, reduced: false, webgl: true, dpr: 1, dark: true }, opts || {});
  const listeners = {};
  const rafQueue = [];
  const win = {
    document,
    innerWidth: o.width,
    innerHeight: 800,
    devicePixelRatio: o.dpr,
    addEventListener: jest.fn((type, fn) => { listeners[type] = fn; }),
    removeEventListener: jest.fn(),
    getComputedStyle: () => ({
      getPropertyValue: (name) => ({ "--bg": "#17141F", "--ink": "#ECE8F5", "--accent": "#A08CFF" }[name] || ""),
    }),
    matchMedia: (q) => ({
      matches: q.includes("prefers-reduced-motion") ? o.reduced
        : q.includes("pointer: coarse") ? o.coarse
        : q.includes("prefers-color-scheme: dark") ? o.dark : false,
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
    }),
    requestAnimationFrame: jest.fn((fn) => { rafQueue.push(fn); return rafQueue.length; }),
    cancelAnimationFrame: jest.fn(),
  };
  const env = { win, listeners, opts: o };
  // Runs the next scheduled animation frame at time t (ms). Returns false if none is pending.
  env.frame = (t) => {
    const fn = rafQueue.shift();
    if (!fn) return false;
    fn(t);
    return true;
  };
  env.pending = () => rafQueue.length;
  env.clearFrames = () => { rafQueue.length = 0; };
  // Runs frames at a fixed spacing for a span of time.
  env.run = (fromMs, toMs, stepMs) => {
    for (let t = fromMs; t <= toMs; t += stepMs) if (!env.frame(t)) break;
  };
  document.body.innerHTML = '<div class="wrap"><h1>Hello</h1></div>';
  document.documentElement.removeAttribute("data-theme");
  HTMLCanvasElement.prototype.getContext = jest.fn(() => (o.webgl ? { getExtension: () => null } : null));
  return env;
}

function setup(envOpts) {
  const env = makeEnv(envOpts);
  const three = fakeThree();
  const loadThree = jest.fn(async () => three.THREE);
  const startOpts = { window: env.win, loadThree };
  return { env, three, loadThree, startOpts, start: () => city.start(startOpts) };
}

beforeEach(() => { jest.spyOn(console, "warn").mockImplementation(() => {}); });
afterEach(() => { jest.restoreAllMocks(); document.body.innerHTML = ""; });

// ---- device choice -----------------------------------------------------------------

describe("which background a device gets", () => {
  test("a narrow viewport with a coarse pointer gets the static image and never loads three.js", async () => {
    const s = setup({ width: 390, coarse: true });
    const ctl = await s.start();
    expect(ctl.mode).toBe("still");
    expect(ctl.reason).toBe("phone");
    expect(s.loadThree).not.toHaveBeenCalled();
    expect(document.querySelector("#cityBg img.city-still")).not.toBeNull();
    expect(document.querySelector("#cityBg canvas")).toBeNull();
    expect(s.env.win.requestAnimationFrame).not.toHaveBeenCalled();
  });

  test("a desktop viewport gets the canvas and loads three.js", async () => {
    const s = setup({ width: 1280 });
    const ctl = await s.start();
    expect(ctl.mode).toBe("animated");
    expect(s.loadThree).toHaveBeenCalledTimes(1);
    expect(document.querySelector("#cityBg canvas")).not.toBeNull();
    expect(document.querySelector("#cityBg img")).toBeNull();
  });

  test("a narrow viewport with a fine pointer (a small desktop window) still gets the animation", async () => {
    const s = setup({ width: 500, coarse: false });
    const ctl = await s.start();
    expect(ctl.mode).toBe("animated");
    expect(s.loadThree).toHaveBeenCalled();
  });

  test("a tablet (768px wide, coarse pointer) gets the animation", async () => {
    const s = setup({ width: 768, coarse: true });
    expect((await s.start()).mode).toBe("animated");
  });

  test("a wide viewport with a coarse pointer gets the animation", async () => {
    const s = setup({ width: 1024, coarse: true });
    expect((await s.start()).mode).toBe("animated");
  });

  test("prefers-reduced-motion gets the static image on a desktop", async () => {
    const s = setup({ width: 1280, reduced: true });
    const ctl = await s.start();
    expect(ctl.mode).toBe("still");
    expect(ctl.reason).toBe("reduced-motion");
    expect(s.loadThree).not.toHaveBeenCalled();
  });

  test("prefers-reduced-motion gets the static image on a phone too", async () => {
    const s = setup({ width: 390, coarse: true, reduced: true });
    expect((await s.start()).mode).toBe("still");
    expect(s.loadThree).not.toHaveBeenCalled();
  });

  test("no WebGL gets the static image, without throwing, and never loads three.js", async () => {
    const s = setup({ webgl: false });
    const ctl = await s.start();
    expect(ctl.mode).toBe("still");
    expect(ctl.reason).toBe("no-webgl");
    expect(s.loadThree).not.toHaveBeenCalled();
    expect(document.querySelector("#cityBg img")).not.toBeNull();
  });

  test("a getContext that throws counts as no WebGL", async () => {
    const s = setup();
    HTMLCanvasElement.prototype.getContext = jest.fn(() => { throw new Error("boom"); });
    expect((await s.start()).reason).toBe("no-webgl");
  });

  test("a failed three.js download falls back to the static image", async () => {
    const s = setup();
    s.startOpts.loadThree = jest.fn(async () => { throw new Error("404"); });
    const ctl = await city.start(s.startOpts);
    expect(ctl.mode).toBe("still");
    expect(ctl.reason).toBe("load-failed");
    expect(document.querySelector("#cityBg canvas")).toBeNull();
  });

  test("a renderer that cannot be created falls back to the static image", async () => {
    const s = setup();
    s.three.THREE.WebGLRenderer = function () { throw new Error("no context"); };
    const ctl = await s.start();
    expect(ctl.mode).toBe("still");
    expect(ctl.reason).toBe("no-webgl");
  });

  test("a failed three.js download logs a console error that names the step", async () => {
    const err = jest.spyOn(console, "error").mockImplementation(() => {});
    const s = setup();
    s.startOpts.loadThree = jest.fn(async () => { throw new Error("404"); });
    await city.start(s.startOpts);
    expect(err).toHaveBeenCalledTimes(1);
    expect(err.mock.calls[0].join(" ")).toMatch(/city background.*load three\.js.*404/i);
  });

  test("a renderer that cannot be created logs a console error that names the step", async () => {
    const err = jest.spyOn(console, "error").mockImplementation(() => {});
    const s = setup();
    s.three.THREE.WebGLRenderer = function () { throw new Error("no context"); };
    await s.start();
    expect(err.mock.calls.map((c) => c.join(" ")).join("\n")).toMatch(/city background.*create the scene.*no context/i);
  });

  test("a first render that throws falls back to the static image and logs the step", async () => {
    const err = jest.spyOn(console, "error").mockImplementation(() => {});
    const s = setup();
    s.three.THREE.WebGLRenderer.prototype.render = function () { throw new Error("shader failed"); };
    const ctl = await s.start();
    expect(ctl.mode).toBe("still");
    expect(ctl.reason).toBe("render-failed");
    expect(document.querySelector("#cityBg canvas")).toBeNull();
    expect(document.querySelector("#cityBg img")).not.toBeNull();
    expect(err.mock.calls.map((c) => c.join(" ")).join("\n")).toMatch(/city background.*render.*shader failed/i);
  });

  test("a render that throws in a later frame also falls back to the static image", async () => {
    const err = jest.spyOn(console, "error").mockImplementation(() => {});
    const s = setup();
    const ctl = await s.start();
    expect(ctl.mode).toBe("animated");
    s.three.made.renderers[0].render = () => { throw new Error("context gone"); };
    s.env.frame(100);
    expect(ctl.mode).toBe("still");
    expect(ctl.reason).toBe("render-failed");
    expect(err).toHaveBeenCalled();
  });

  test("the mode and reason are recorded on the container", async () => {
    const s = setup({ width: 390, coarse: true });
    await s.start();
    const bg = document.getElementById("cityBg");
    expect(bg.dataset.cityMode).toBe("still");
    expect(bg.dataset.cityReason).toBe("phone");
    expect(bg.getAttribute("aria-hidden")).toBe("true");
  });

  test("the container is the first thing in the body, with a scrim", async () => {
    const s = setup();
    await s.start();
    expect(document.body.firstElementChild.id).toBe("cityBg");
    expect(document.querySelector("#cityBg .city-scrim")).not.toBeNull();
  });

  test("starting twice leaves one background", async () => {
    const s = setup();
    await s.start();
    await s.start();
    expect(document.querySelectorAll("#cityBg").length).toBe(1);
  });
});

// ---- static image ------------------------------------------------------------------

describe("the static image", () => {
  test("is a picture with a WebP source and a JPEG fallback, and no alt text", async () => {
    const s = setup({ width: 390, coarse: true });
    await s.start();
    const source = document.querySelector("#cityBg picture source");
    const img = document.querySelector("#cityBg picture img");
    expect(source.getAttribute("type")).toBe("image/webp");
    expect(source.getAttribute("srcset")).toMatch(/\.webp$/);
    expect(img.getAttribute("src")).toMatch(/\.jpg$/);
    expect(img.getAttribute("alt")).toBe("");
  });

  test("always uses the dark still, whatever the system setting", async () => {
    const s = setup({ width: 390, coarse: true, dark: false });
    await s.start();
    expect(document.querySelector("#cityBg img").getAttribute("src")).toBe("/public/city-still-dark.jpg");
    expect(document.querySelector("#cityBg source").getAttribute("srcset")).toBe("/public/city-still-dark.webp");
  });

  test("an explicit data-theme does not change the still", async () => {
    const s = setup({ width: 390, coarse: true, dark: true });
    document.documentElement.setAttribute("data-theme", "light");
    await s.start();
    expect(document.querySelector("#cityBg img").getAttribute("src")).toBe("/public/city-still-dark.jpg");
  });
});

// ---- frame rate, cap, pixel ratio ---------------------------------------------------

describe("the render loop", () => {
  test("caps the frame rate at 30 even when the display runs at 60", async () => {
    const s = setup();
    await s.start();
    const renderer = s.three.made.renderers[0];
    s.env.run(0, 1000, 1000 / 60);
    expect(renderer.renders).toBeGreaterThanOrEqual(28);
    expect(renderer.renders).toBeLessThanOrEqual(32);
  });

  test("a steady 60 frames a second stays animated", async () => {
    const s = setup();
    const ctl = await s.start();
    s.env.run(0, 8000, 1000 / 60);
    expect(ctl.mode).toBe("animated");
  });

  test("caps devicePixelRatio at 2", async () => {
    const s = setup({ dpr: 3 });
    await s.start();
    expect(s.three.made.renderers[0].pixelRatio).toBe(2);
  });

  test("keeps a device pixel ratio of 1", async () => {
    const s = setup({ dpr: 1 });
    await s.start();
    expect(s.three.made.renderers[0].pixelRatio).toBe(1);
  });

  test("a sustained low frame rate swaps to the static image, disposes the renderer, and records why", async () => {
    const s = setup();
    const ctl = await s.start();
    const { renderers, geometries, materials } = s.three.made;
    s.env.run(0, 14000, 100); // 10 frames a second
    expect(ctl.mode).toBe("still");
    expect(ctl.reason).toBe("low-fps");
    expect(renderers[0].dispose).toHaveBeenCalled();
    geometries.forEach((g) => expect(g.dispose).toHaveBeenCalled());
    materials.forEach((m) => expect(m.dispose).toHaveBeenCalled());
    expect(document.querySelector("#cityBg canvas")).toBeNull();
    expect(document.querySelector("#cityBg img.city-still")).not.toBeNull();
    const bg = document.getElementById("cityBg");
    expect(bg.dataset.cityMode).toBe("still");
    expect(bg.dataset.cityReason).toBe("low-fps");
    expect(s.env.pending()).toBe(0); // the loop is over
  });

  test("software-WebGL speeds (about 18 fps) keep the animation", async () => {
    const s = setup();
    const ctl = await s.start();
    s.env.run(0, 30000, 1000 / 18);
    expect(ctl.mode).toBe("animated");
  });

  test("a slow stretch shorter than 10 seconds does not swap", async () => {
    const s = setup();
    const ctl = await s.start();
    s.env.run(0, 8000, 100); // 8 s at 10 fps
    s.env.run(8100, 16000, 1000 / 60); // then healthy
    expect(ctl.mode).toBe("animated");
  });

  test("a hidden tab stops the loop and a visible one resumes it", async () => {
    const s = setup();
    const ctl = await s.start();
    s.env.run(0, 100, 1000 / 60);
    const renderer = s.three.made.renderers[0];

    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    s.env.listeners.visibilitychange && s.env.listeners.visibilitychange();
    document.dispatchEvent(new Event("visibilitychange"));
    expect(s.env.win.cancelAnimationFrame).toHaveBeenCalled();
    s.env.clearFrames();
    const before = renderer.renders;
    expect(s.env.frame(500)).toBe(false); // nothing is scheduled
    expect(renderer.renders).toBe(before);

    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(s.env.pending()).toBe(1);
    s.env.run(10000, 10200, 1000 / 60);
    expect(renderer.renders).toBeGreaterThan(before);
    expect(ctl.mode).toBe("animated");
  });

  test("time spent hidden does not count as a low frame rate", async () => {
    const s = setup();
    const ctl = await s.start();
    s.env.run(0, 500, 1000 / 60);
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
    s.env.clearFrames();
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
    s.env.run(60000, 64000, 1000 / 60);
    expect(ctl.mode).toBe("animated");
  });

  test("a lost WebGL context swaps to the static image", async () => {
    const s = setup();
    const ctl = await s.start();
    const canvas = document.querySelector("#cityBg canvas");
    const evt = new Event("webglcontextlost", { cancelable: true });
    canvas.dispatchEvent(evt);
    expect(evt.defaultPrevented).toBe(true);
    expect(ctl.mode).toBe("still");
    expect(ctl.reason).toBe("context-lost");
    expect(s.three.made.renderers[0].dispose).toHaveBeenCalled();
  });

  test("dispose is safe to call twice and stops the loop", async () => {
    const s = setup();
    const ctl = await s.start();
    ctl.dispose();
    ctl.dispose();
    expect(s.three.made.renderers[0].dispose).toHaveBeenCalledTimes(1);
    expect(document.getElementById("cityBg")).toBeNull();
  });

  test("the renderer starts at the window size, not the size of any element", async () => {
    const s = setup({ width: 1920 });
    s.env.win.innerHeight = 1080;
    await s.start();
    expect(s.three.made.renderers[0].size).toEqual([1920, 1080, false]);
  });

  test("a resize sizes the renderer and the camera from the window, ignoring the container", async () => {
    const s = setup();
    await s.start();
    const renderer = s.three.made.renderers[0];
    const bg = document.getElementById("cityBg");
    Object.defineProperty(bg, "clientWidth", { configurable: true, value: 900 });
    Object.defineProperty(bg, "clientHeight", { configurable: true, value: 400 });
    s.env.win.innerWidth = 1600;
    s.env.win.innerHeight = 900;
    s.env.listeners.resize();
    expect(renderer.size).toEqual([1600, 900, false]);
  });

  test("a theme change recolors the scene without rebuilding it", async () => {
    const s = setup();
    await s.start();
    const meshesBefore = s.three.made.meshes.length;
    s.env.win.getComputedStyle = () => ({
      getPropertyValue: (name) => ({ "--bg": "#F2F0F7", "--ink": "#221E33", "--accent": "#5B3FD6" }[name] || ""),
    });
    s.env.listeners.themechange && s.env.listeners.themechange();
    document.documentElement.setAttribute("data-theme", "light");
    await Promise.resolve();
    expect(s.three.made.meshes.length).toBe(meshesBefore);
  });
});

// ---- the scene ---------------------------------------------------------------------

describe("the scene", () => {
  test("uses two instanced meshes, so the city is two draw calls", () => {
    const { THREE, made } = fakeThree();
    city.createScene(THREE, { canvas: document.createElement("canvas"), width: 800, height: 400, dpr: 1, colors: city.deriveColors({ bg: "#17141F", ink: "#ECE8F5", accent: "#A08CFF" }) });
    expect(made.meshes.length).toBe(2);
    expect(made.meshes[0].count).toBeGreaterThan(50);
  });

  test("the scene has fog in the theme's background color", () => {
    const { THREE } = fakeThree();
    const colors = city.deriveColors({ bg: "#17141F", ink: "#ECE8F5", accent: "#A08CFF" });
    const sc = city.createScene(THREE, { canvas: document.createElement("canvas"), width: 800, height: 400, dpr: 1, colors });
    expect(sc.scene.fog).not.toBeNull();
    expect(sc.scene.fog.color.v).toBe(colors.fog);
  });

  test("step moves the grid, recycles rows, and marks the instance matrices for upload", () => {
    const { THREE, made } = fakeThree();
    const sc = city.createScene(THREE, { canvas: document.createElement("canvas"), width: 800, height: 400, dpr: 1, colors: city.deriveColors({ bg: "#17141F", ink: "#ECE8F5", accent: "#A08CFF" }) });
    made.meshes.forEach((m) => { m.instanceMatrix.needsUpdate = false; });
    sc.step(10000); // far enough to recycle many rows
    expect(made.meshes.every((m) => m.instanceMatrix.needsUpdate)).toBe(true);
    expect(sc.renderer.renders).toBe(0);
    sc.render();
    expect(sc.renderer.renders).toBe(1);
  });

  test("dispose releases geometry, materials and the renderer", () => {
    const { THREE, made } = fakeThree();
    const sc = city.createScene(THREE, { canvas: document.createElement("canvas"), width: 800, height: 400, dpr: 1, colors: city.deriveColors({ bg: "#17141F", ink: "#ECE8F5", accent: "#A08CFF" }) });
    sc.dispose();
    expect(made.renderers[0].dispose).toHaveBeenCalled();
    expect(made.renderers[0].forceContextLoss).toHaveBeenCalled();
    made.geometries.forEach((g) => expect(g.dispose).toHaveBeenCalled());
    made.materials.forEach((m) => expect(m.dispose).toHaveBeenCalled());
  });

  test("resize updates the camera aspect", () => {
    const { THREE } = fakeThree();
    const sc = city.createScene(THREE, { canvas: document.createElement("canvas"), width: 800, height: 400, dpr: 1, colors: city.deriveColors({ bg: "#17141F", ink: "#ECE8F5", accent: "#A08CFF" }) });
    sc.resize(1000, 250);
    expect(sc.camera.aspect).toBe(4);
  });

  test("the window material is unlit, so the strips glow", () => {
    const { THREE, made } = fakeThree();
    city.createScene(THREE, { canvas: document.createElement("canvas"), width: 800, height: 400, dpr: 1, colors: city.deriveColors({ bg: "#17141F", ink: "#ECE8F5", accent: "#A08CFF" }) });
    expect(made.meshes[1].material).toBeInstanceOf(THREE.MeshBasicMaterial);
  });
});

// ---- the camera: a slow drift down a street canyon -----------------------------------------

describe("the camera", () => {
  const colors = () => city.deriveColors({ bg: "#17141F", ink: "#ECE8F5", accent: "#A08CFF" });
  const make = () => {
    const { THREE, made } = fakeThree();
    const sc = city.createScene(THREE, { canvas: document.createElement("canvas"), width: 1280, height: 460, dpr: 1, colors: colors() });
    return { sc, made };
  };
  const C = city.CAMERA;

  test("the named constants match the approved framing", () => {
    expect(C.FOV).toBe(55);
    expect(C.TILT_DEGREES).toBeGreaterThanOrEqual(20);
    expect(C.TILT_DEGREES).toBeLessThanOrEqual(25);
    expect(C.HEIGHT / C.TOWER_HEIGHT).toBeGreaterThanOrEqual(0.2);
    expect(C.HEIGHT / C.TOWER_HEIGHT).toBeLessThanOrEqual(0.25);
    expect(C.BLOCK_SECONDS).toBe(27); // halved from 13.5
    expect(C.SPEED * C.BLOCK_SECONDS).toBeCloseTo(city.SPACING);
  });

  test("forward travel over a fixed interval is half the previous speed", () => {
    const PREVIOUS_SPEED = city.SPACING / 13.5;
    const { sc } = make();
    const z0 = sc.camera.position.z;
    sc.step(C.SPEED * 20); // twenty seconds at the configured rate
    expect(z0 - sc.camera.position.z).toBeCloseTo(PREVIOUS_SPEED * 10, 6); // half of the old 20 s travel
    expect(C.SPEED).toBeCloseTo(PREVIOUS_SPEED / 2, 9);
  });

  test("drift rates are halved (cycles twice as long) and drift amplitudes are unchanged", () => {
    expect(C.DRIFT_X_SECONDS).toBe(190);
    expect(C.DRIFT_Y_SECONDS).toBe(140);
    expect(C.DRIFT_X).toBe(1.2);
    expect(C.DRIFT_Y).toBe(0.6);
  });

  test("uses a 55 degree field of view", () => {
    expect(make().sc.camera.fov).toBe(55);
  });

  test("starts inside the canyon, low, on the street axis", () => {
    const { sc } = make();
    expect(sc.camera.position.y).toBeLessThan(C.TOWER_HEIGHT * 0.3);
    expect(Math.abs(sc.camera.position.x)).toBeLessThanOrEqual(C.DRIFT_X);
  });

  test("moves forward along -z at exactly the configured rate", () => {
    const { sc } = make();
    const z0 = sc.camera.position.z;
    for (let i = 0; i < 300; i++) sc.step(C.SPEED / 30); // ten seconds at 30 frames a second
    expect(z0 - sc.camera.position.z).toBeCloseTo(C.SPEED * 10, 6);
  });

  test("one block passes in the configured number of seconds", () => {
    const { sc } = make();
    const z0 = sc.camera.position.z;
    sc.step(C.SPEED * C.BLOCK_SECONDS);
    expect(z0 - sc.camera.position.z).toBeCloseTo(city.SPACING, 6);
  });

  test("height stays within the configured drift range and the sideways drift within its range", () => {
    const { sc } = make();
    let minY = Infinity, maxY = -Infinity, maxX = 0;
    for (let i = 0; i < 20000; i++) {
      sc.step(C.SPEED * 0.5);
      minY = Math.min(minY, sc.camera.position.y);
      maxY = Math.max(maxY, sc.camera.position.y);
      maxX = Math.max(maxX, Math.abs(sc.camera.position.x));
    }
    expect(minY).toBeGreaterThanOrEqual(C.HEIGHT - C.DRIFT_Y - 1e-9);
    expect(maxY).toBeLessThanOrEqual(C.HEIGHT + C.DRIFT_Y + 1e-9);
    expect(maxY - minY).toBeGreaterThan(C.DRIFT_Y); // it really does drift
    expect(maxX).toBeLessThanOrEqual(C.DRIFT_X + 1e-9);
    expect(maxX).toBeGreaterThan(C.DRIFT_X / 2);
  });

  test("tilts down by the configured angle, with no sideways turn, and keeps it while moving", () => {
    const { sc } = make();
    const expected = -(C.TILT_DEGREES * Math.PI) / 180;
    expect(sc.camera.rotation.x).toBeCloseTo(expected, 9);
    expect(sc.camera.rotation.y).toBe(0);
    sc.step(500);
    expect(sc.camera.rotation.x).toBeCloseTo(expected, 9);
    expect(sc.camera.rotation.y).toBe(0);
  });

  test("the running animation moves the camera at the configured rate in real time", async () => {
    const s = setup();
    await s.start();
    const cam = s.three.made.cameras[0];
    const z0 = cam.position.z;
    s.env.run(0, 10000, 1000 / 60);
    expect(z0 - cam.position.z).toBeCloseTo(C.SPEED * 10, 0);
  });

  test("blocks recycle behind the camera and the count stays bounded over a long run", () => {
    const { sc, made } = make();
    const rows = sc.grid.rows.length;
    const instances = made.meshes.map((m) => m.count);
    const hours = 2;
    const frames = hours * 3600 * 30;
    for (let i = 0; i < frames; i++) {
      sc.step(C.SPEED / 30);
      if (i % 5000 === 0) {
        expect(sc.grid.rows.length).toBe(rows);
        // the nearest row is at or just behind the camera; every other row is ahead of it
        const nearest = made.meshes[0].position.z;
        expect(nearest).toBeGreaterThanOrEqual(sc.camera.position.z);
        expect(nearest - sc.camera.position.z).toBeLessThan(city.SPACING);
      }
    }
    expect(sc.grid.rows.length).toBe(rows);
    expect(made.meshes.map((m) => m.count)).toEqual(instances);
    expect(sc.grid.recycled).toBe(Math.floor((C.SPEED * hours * 3600) / city.SPACING + 1e-6));
    expect(sc.grid.recycled).toBeGreaterThan(250); // 2 h at one block per 27 s is 266 rows
  });

  test("fog ends well before the far end of the street", () => {
    const { sc } = make();
    expect(sc.scene.fog.far).toBeLessThan(sc.camera.far);
    expect(sc.scene.fog.far).toBeLessThan(city.SPACING * sc.grid.rows.length);
  });
});

// ---- the grid ----------------------------------------------------------------------

describe("the building grid", () => {
  const opts = { cols: 8, rows: 6, spacing: 10, seed: 7 };

  test("has the requested rows and columns, deterministic for a seed", () => {
    const a = city.createGrid(opts);
    const b = city.createGrid(opts);
    expect(a.rows.length).toBe(6);
    expect(a.rows.every((r) => r.length === 8)).toBe(true);
    expect(a.rows).toEqual(b.rows);
    expect(city.createGrid(Object.assign({}, opts, { seed: 8 })).rows).not.toEqual(a.rows);
  });

  test("buildings sit inside their cell, leave a clear avenue, and have window strips", () => {
    const g = city.createGrid(opts);
    for (const row of g.rows) {
      for (const b of row) {
        expect(b.h).toBeGreaterThan(0);
        expect(b.w).toBeLessThan(opts.spacing);
        expect(b.d).toBeLessThan(opts.spacing);
        expect(Math.abs(b.x)).toBeGreaterThan(b.w / 2); // none straddles the camera's path
        expect(b.windows.length).toBeLessThanOrEqual(city.MAX_WINDOWS);
        for (const w of b.windows) { expect(w.y).toBeGreaterThan(0); expect(w.y).toBeLessThan(b.h); }
      }
    }
  });

  test("row positions step back from the camera", () => {
    const g = city.createGrid(opts);
    expect(g.rowZ(0)).toBe(0);
    expect(g.rowZ(2)).toBe(-20);
    g.advance(3);
    expect(g.rowZ(0)).toBe(3);
  });

  test("advancing less than a row recycles nothing", () => {
    const g = city.createGrid(opts);
    const first = g.rows.slice();
    expect(g.advance(4)).toBe(0);
    expect(g.rows).toEqual(first);
  });

  test("advancing past a row drops the row behind the camera and adds one at the far end", () => {
    const g = city.createGrid(opts);
    const before = g.rows.map((r) => r);
    expect(g.advance(11)).toBe(1);
    expect(g.rows.length).toBe(6);
    expect(g.offset).toBeCloseTo(1);
    expect(g.rows.slice(0, 5)).toEqual(before.slice(1));
    expect(g.rows[5]).not.toEqual(before[0]);
  });

  test("advancing several rows at once recycles them all and keeps the offset under one row", () => {
    const g = city.createGrid(opts);
    expect(g.advance(35)).toBe(3);
    expect(g.offset).toBeCloseTo(5);
    expect(g.rows.length).toBe(6);
  });

  test("loops forever: many rows later the grid is still full", () => {
    const g = city.createGrid(opts);
    let recycled = 0;
    for (let i = 0; i < 1000; i++) recycled += g.advance(2.5);
    expect(recycled).toBe(250);
    expect(g.rows.length).toBe(6);
    expect(g.rows.every((r) => r.length === 8)).toBe(true);
  });
});

// ---- colors from the theme's CSS tokens ------------------------------------------------

describe("theme colors", () => {
  test("parses 6-digit and 3-digit hex and rgb()", () => {
    expect(city.parseColor("#A08CFF")).toEqual([160, 140, 255]);
    expect(city.parseColor("#fff")).toEqual([255, 255, 255]);
    expect(city.parseColor(" rgb(10, 20, 30) ")).toEqual([10, 20, 30]);
  });

  test("returns null for anything else", () => {
    expect(city.parseColor("")).toBeNull();
    expect(city.parseColor("banana")).toBeNull();
    expect(city.parseColor(undefined)).toBeNull();
  });

  test("fog is the page background and windows are the accent", () => {
    const c = city.deriveColors({ bg: "#17141F", ink: "#ECE8F5", accent: "#A08CFF" });
    expect(c.fog).toBe("#17141f");
    expect(c.window).toBe("#a08cff");
  });

  test("buildings sit between the background and the text color, closer to the background", () => {
    const dark = city.deriveColors({ bg: "#17141F", ink: "#ECE8F5", accent: "#A08CFF" });
    const [r] = city.parseColor(dark.building);
    expect(r).toBeGreaterThan(0x17);
    expect(r).toBeLessThan(0x80);
    const light = city.deriveColors({ bg: "#F2F0F7", ink: "#221E33", accent: "#5B3FD6" });
    const [lr] = city.parseColor(light.building);
    expect(lr).toBeLessThan(0xf2);
    expect(lr).toBeGreaterThan(0x80);
  });

  test("falls back to the dark theme when a token is missing", () => {
    const c = city.deriveColors({});
    expect(c.fog).toBe("#17141f");
    expect(c.window).toBe("#a08cff");
  });

  test("readTokens reads the CSS custom properties", () => {
    const style = { getPropertyValue: (n) => ({ "--bg": " #111111 ", "--ink": "#eeeeee", "--accent": "#abcdef" }[n]) };
    expect(city.readTokens(style)).toEqual({ bg: "#111111", ink: "#eeeeee", accent: "#abcdef" });
  });
});

// ---- the frame rate monitor --------------------------------------------------------------

describe("the frame rate monitor", () => {
  function run(monitor, fps, fromMs, toMs) {
    for (let t = fromMs; t <= toMs; t += 1000 / fps) monitor.tick(t);
  }

  test("the named settings are 15 fps for 10 seconds", () => {
    expect(city.LOW_FPS).toBe(15);
    expect(city.LOW_FPS_SECONDS).toBe(10);
  });

  test("fires after 10 seconds below 15 fps", () => {
    const onLow = jest.fn();
    const m = city.createFpsMonitor({ onLow });
    run(m, 10, 0, 12000);
    expect(onLow).toHaveBeenCalledTimes(1);
    expect(onLow.mock.calls[0][0]).toBeLessThan(15);
  });

  test("does not fire at 18 fps", () => {
    const onLow = jest.fn();
    const m = city.createFpsMonitor({ onLow });
    run(m, 18, 0, 30000);
    expect(onLow).not.toHaveBeenCalled();
  });

  test("does not fire at 60 fps", () => {
    const onLow = jest.fn();
    const m = city.createFpsMonitor({ onLow });
    run(m, 60, 0, 10000);
    expect(onLow).not.toHaveBeenCalled();
  });

  test("does not fire at exactly 15 fps", () => {
    const onLow = jest.fn();
    const m = city.createFpsMonitor({ onLow });
    run(m, 15, 0, 20000);
    expect(onLow).not.toHaveBeenCalled();
  });

  test("one healthy second restarts the count", () => {
    const onLow = jest.fn();
    const m = city.createFpsMonitor({ onLow });
    run(m, 10, 0, 8500);
    run(m, 60, 8517, 9700);
    run(m, 10, 9717, 16000);
    expect(onLow).not.toHaveBeenCalled();
  });

  test("reset forgets a slow stretch", () => {
    const onLow = jest.fn();
    const m = city.createFpsMonitor({ onLow });
    run(m, 10, 0, 8500);
    m.reset();
    run(m, 10, 20000, 28500);
    expect(onLow).not.toHaveBeenCalled();
  });
});
