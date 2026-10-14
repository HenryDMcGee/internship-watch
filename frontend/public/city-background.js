// The low-poly night city behind the header, on the dashboard and the public board.
//
// Phones, reduced-motion visitors, browsers without WebGL, and slow machines get one still frame
// of the same scene instead (made by scripts/render-city-still.js). three.js is a separate file,
// loaded only after the device check passes, so a phone never downloads it.
//
// The page carries this file with `<script src="/public/city-background.js" defer>`. It lives
// under /public/ because that is the part of the site the public hostname is allowed to serve.
(function () {
  var THREE_URL = "/public/vendor/three.min.mjs";
  var STILL_BASE = "/public/city-still-";
  var PHONE_MAX_WIDTH = 768; // narrower than this, with a coarse pointer, is a phone
  var FRAME_MS = 1000 / 30; // frame rate cap
  var MAX_DPR = 2;
  var LOW_FPS = 15; // swap to the still only when the frame rate stays below this...
  var LOW_FPS_SECONDS = 10; // ...for this many seconds in a row (software WebGL runs in the teens)
  var STILL_THEME = "dark"; // the site has one palette, so the still is always the dark one
  var DEFAULT_HEIGHT = 460; // matches .city-bg in style.css, used when the box has no size yet

  var COLS = 14;
  var ROWS = 18;
  var SPACING = 10; // one building block, front to back

  // The camera drifts down the avenue between the towers, low and looking slightly down.
  var TOWER_HEIGHT = 24; // the tallest a building can be (see createGrid)
  var CAMERA = {
    TOWER_HEIGHT: TOWER_HEIGHT,
    HEIGHT: TOWER_HEIGHT * 0.23, // 20 to 25 percent of the tallest towers, below most rooftops
    TILT_DEGREES: 20, // downward, from horizontal
    FOV: 55,
    BLOCK_SECONDS: 13.5, // one block passes every 12 to 15 seconds: barely moving
    SPEED: SPACING / 13.5, // world units per second, forward along -z
    DRIFT_X: 1.2, // sideways drift, units either side of the avenue's middle
    DRIFT_Y: 0.6, // vertical drift, units either side of HEIGHT
    DRIFT_X_SECONDS: 95, // each drift runs on its own long cycle
    DRIFT_Y_SECONDS: 70,
  };
  var FOG_FAR = SPACING * (ROWS - 5); // the far end of the street fades out before the last rows
  var MAX_WINDOWS = 6;
  var SEED = 2027;

  var FALLBACK_TOKENS = { bg: "#17141F", ink: "#ECE8F5", accent: "#A08CFF" }; // the dark theme in style.css
  var BUILDING_MIX = 0.12; // how far a building color sits from the background toward the text color

  var current = null;

  // ---- colors from the CSS tokens ----------------------------------------------------

  function parseColor(text) {
    var s = String(text || "").trim().toLowerCase();
    var m = /^#([0-9a-f]{3})$/.exec(s);
    if (m) return [0, 1, 2].map(function (i) { return parseInt(m[1][i] + m[1][i], 16); });
    m = /^#([0-9a-f]{6})$/.exec(s);
    if (m) return [0, 2, 4].map(function (i) { return parseInt(m[1].slice(i, i + 2), 16); });
    m = /^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$/.exec(s);
    if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
    return null;
  }

  function toHex(rgb) {
    return "#" + rgb.map(function (n) {
      var v = Math.max(0, Math.min(255, Math.round(n)));
      return (v < 16 ? "0" : "") + v.toString(16);
    }).join("");
  }

  function readTokens(style) {
    function get(name) { return String(style.getPropertyValue(name) || "").trim(); }
    return { bg: get("--bg"), ink: get("--ink"), accent: get("--accent") };
  }

  // Fog is the page background, windows are the accent, buildings sit just off the background
  // toward the text color, so they read dark in the dark theme and as soft shadows in the light one.
  function deriveColors(tokens) {
    var t = tokens || {};
    var bg = parseColor(t.bg) || parseColor(FALLBACK_TOKENS.bg);
    var ink = parseColor(t.ink) || parseColor(FALLBACK_TOKENS.ink);
    var accent = parseColor(t.accent) || parseColor(FALLBACK_TOKENS.accent);
    return {
      fog: toHex(bg),
      window: toHex(accent),
      building: toHex(bg.map(function (c, i) { return c + (ink[i] - c) * BUILDING_MIX; })),
    };
  }

  // ---- the grid of buildings ----------------------------------------------------------

  function makeRandom(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // Rows run away from the camera. Row i sits at z = offset - i * spacing. The columns are
  // an even count, so the middle is a clear avenue the camera drifts down. When the nearest row
  // passes behind the camera it is dropped and a new one is added at the far end.
  function createGrid(opts) {
    var cols = opts.cols;
    var spacing = opts.spacing;
    var random = makeRandom(opts.seed);
    var half = (cols - 1) / 2;

    function makeRow() {
      var row = [];
      for (var c = 0; c < cols; c++) {
        var side = c < half ? -1 : 1;
        var h = 3 + Math.pow(random(), 1.6) * (TOWER_HEIGHT - 3);
        var building = {
          x: (c - half) * spacing + side * spacing * 0.3, // the extra widens the avenue
          w: spacing * (0.45 + random() * 0.35),
          d: spacing * (0.45 + random() * 0.35),
          h: h,
          windows: [],
        };
        var n = 3 + Math.floor(random() * (MAX_WINDOWS - 2));
        for (var k = 0; k < n; k++) {
          building.windows.push({ face: random() < 0.6 ? "front" : "side", y: 1 + random() * (h - 1.6) });
        }
        row.push(building);
      }
      return row;
    }

    var grid = { rows: [], offset: 0, recycled: 0, spacing: spacing, cols: cols };
    for (var r = 0; r < opts.rows; r++) grid.rows.push(makeRow());
    grid.rowZ = function (i) { return grid.offset - i * spacing; };
    grid.advance = function (distance) {
      grid.offset += distance;
      var recycled = 0;
      while (grid.offset >= spacing) {
        grid.offset -= spacing;
        grid.rows.shift();
        grid.rows.push(makeRow());
        recycled += 1;
        grid.recycled += 1;
      }
      return recycled;
    };
    return grid;
  }

  // ---- the three.js scene ---------------------------------------------------------------

  // Two InstancedMeshes (buildings, window strips): the whole city is two draw calls.
  function createScene(THREE, opts) {
    var renderer = new THREE.WebGLRenderer({
      canvas: opts.canvas,
      antialias: true,
      powerPreference: "low-power",
      preserveDrawingBuffer: !!opts.preserveDrawingBuffer,
    });
    renderer.setPixelRatio(Math.min(opts.dpr || 1, MAX_DPR));

    var scene = new THREE.Scene();
    scene.fog = new THREE.Fog(opts.colors.fog, SPACING * 2, FOG_FAR);
    renderer.setClearColor(opts.colors.fog);

    var camera = new THREE.PerspectiveCamera(CAMERA.FOV, opts.width / opts.height, 0.1, SPACING * (ROWS + 2));
    camera.rotation.set(-CAMERA.TILT_DEGREES * Math.PI / 180, 0, 0); // down the street axis (-z), tilted down

    var sun = new THREE.DirectionalLight("#ffffff", 1.6);
    sun.position.set(-0.6, 1, 0.5);
    scene.add(sun);
    scene.add(new THREE.AmbientLight("#ffffff", 0.9));

    var grid = createGrid({ cols: COLS, rows: ROWS, spacing: SPACING, seed: opts.seed || SEED });
    var geometry = new THREE.BoxGeometry(1, 1, 1);
    var buildingMaterial = new THREE.MeshLambertMaterial({ color: opts.colors.building });
    var windowMaterial = new THREE.MeshBasicMaterial({ color: opts.colors.window });
    var buildings = new THREE.InstancedMesh(geometry, buildingMaterial, COLS * ROWS);
    var windows = new THREE.InstancedMesh(geometry, windowMaterial, COLS * ROWS * MAX_WINDOWS);
    buildings.frustumCulled = false;
    windows.frustumCulled = false;
    scene.add(buildings);
    scene.add(windows);

    var dummy = new THREE.Object3D();
    function place(mesh, index, x, y, z, sx, sy, sz) {
      dummy.position.set(x, y, z);
      dummy.scale.set(sx, sy, sz);
      dummy.updateMatrix();
      mesh.setMatrixAt(index, dummy.matrix);
    }

    // Writes every instance. Needed at the start and whenever a row is recycled.
    function writeInstances() {
      for (var r = 0; r < grid.rows.length; r++) {
        var z = -r * SPACING;
        for (var c = 0; c < COLS; c++) {
          var b = grid.rows[r][c];
          var i = r * COLS + c;
          place(buildings, i, b.x, b.h / 2, z, b.w, b.h, b.d);
          for (var k = 0; k < MAX_WINDOWS; k++) {
            var slot = i * MAX_WINDOWS + k;
            var win = b.windows[k];
            if (!win) { place(windows, slot, 0, 0, 0, 0, 0, 0); continue; }
            if (win.face === "front") {
              place(windows, slot, b.x, win.y, z + b.d / 2 + 0.03, b.w * 0.7, 0.5, 0.05);
            } else {
              var inward = b.x > 0 ? -1 : 1; // the face toward the avenue
              place(windows, slot, b.x + inward * (b.w / 2 + 0.03), win.y, z, 0.05, 0.5, b.d * 0.7);
            }
          }
        }
      }
      buildings.instanceMatrix.needsUpdate = true;
      windows.instanceMatrix.needsUpdate = true;
    }
    writeInstances();

    var travel = 0; // total distance the camera has moved forward
    var api = {
      scene: scene,
      camera: camera,
      renderer: renderer,
      grid: grid,
      // Moves the camera forward by `distance` world units. Rows that fall behind it are recycled
      // at the far end, and the meshes shift by whole blocks so the instance count never changes.
      step: function (distance) {
        travel += distance;
        if (grid.advance(distance) > 0) writeInstances();
        var meshZ = -grid.recycled * SPACING;
        buildings.position.z = meshZ;
        windows.position.z = meshZ;
        var seconds = travel / CAMERA.SPEED;
        camera.position.set(
          CAMERA.DRIFT_X * Math.sin((seconds / CAMERA.DRIFT_X_SECONDS) * 2 * Math.PI),
          CAMERA.HEIGHT + CAMERA.DRIFT_Y * Math.sin((seconds / CAMERA.DRIFT_Y_SECONDS) * 2 * Math.PI),
          -travel
        );
      },
      render: function () { renderer.render(scene, camera); },
      resize: function (width, height) {
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
      },
      setColors: function (colors) {
        scene.fog.color.set(colors.fog);
        renderer.setClearColor(colors.fog);
        buildingMaterial.color.set(colors.building);
        windowMaterial.color.set(colors.window);
      },
      dispose: function () {
        geometry.dispose();
        buildingMaterial.dispose();
        windowMaterial.dispose();
        renderer.dispose();
        if (renderer.forceContextLoss) renderer.forceContextLoss();
      },
    };
    api.resize(opts.width, opts.height);
    return api;
  }

  // ---- frame rate monitor -----------------------------------------------------------------

  // Fed one tick per animation frame (the display's rate, not our capped render rate). Calls
  // onLow(fps) once the rate has stayed under LOW_FPS for LOW_FPS_SECONDS in a row. A half frame
  // of slack keeps a steady LOW_FPS from tripping it.
  function createFpsMonitor(opts) {
    var threshold = (opts.threshold || LOW_FPS) - 0.5;
    var needMs = (opts.seconds || LOW_FPS_SECONDS) * 1000;
    var start = null;
    var count = 0;
    var lowMs = 0;
    return {
      tick: function (t) {
        if (start === null) { start = t; count = 0; return; }
        count += 1;
        var span = t - start;
        if (span < 1000) return;
        var fps = (count * 1000) / span;
        start = t;
        count = 0;
        if (fps >= threshold) { lowMs = 0; return; }
        lowMs += span;
        if (lowMs >= needMs) { lowMs = 0; opts.onLow(fps); }
      },
      reset: function () { start = null; count = 0; lowMs = 0; },
    };
  }

  // ---- the page ---------------------------------------------------------------------------

  // A failure here must never be silent: name the step in the console, then the caller shows the still image.
  function logFailure(step, error) {
    var detail = error && error.message ? error.message : String(error);
    if (typeof console !== "undefined" && console.error) console.error("City background: " + step + " failed: " + detail, error);
  }

  function mediaMatches(win, query) {
    try { return !!(win.matchMedia && win.matchMedia(query).matches); } catch (e) { return false; }
  }

  function hasWebGL(doc) {
    try {
      var probe = doc.createElement("canvas");
      var gl = probe.getContext("webgl2") || probe.getContext("webgl");
      if (!gl) return false;
      var lose = gl.getExtension && gl.getExtension("WEBGL_lose_context");
      if (lose) lose.loseContext();
      return true;
    } catch (e) {
      return false;
    }
  }

  function start(options) {
    var opts = options || {};
    var win = opts.window || window;
    var doc = win.document;
    var loadThree = opts.loadThree || function () { return import(THREE_URL); };

    if (current) current.dispose();

    var raf = function (fn) { return win.requestAnimationFrame(fn); };
    var caf = function (id) { return win.cancelAnimationFrame(id); };

    var bg = doc.createElement("div");
    bg.id = "cityBg";
    bg.className = "city-bg";
    bg.setAttribute("aria-hidden", "true");
    var scrim = doc.createElement("div");
    scrim.className = "city-scrim";
    bg.appendChild(scrim);
    doc.body.insertBefore(bg, doc.body.firstChild);

    var ctl = { mode: null, reason: null, dispose: dispose };
    var scene = null;
    var canvas = null;
    var picture = null;
    var disposed = false;
    var rafId = null;
    var lastRender = null;
    var lastTick = null;
    var mutationWatcher = null;
    var darkQuery = null;

    function record(mode, reason) {
      ctl.mode = mode;
      ctl.reason = reason || "";
      bg.dataset.cityMode = mode;
      if (reason) bg.dataset.cityReason = reason; else delete bg.dataset.cityReason;
    }

    function themeColors() {
      return deriveColors(readTokens(win.getComputedStyle(doc.documentElement)));
    }

    function applyTheme() {
      if (disposed) return;
      if (scene) scene.setColors(themeColors());
    }

    function showStill(reason) {
      picture = doc.createElement("picture");
      var source = doc.createElement("source");
      source.setAttribute("type", "image/webp");
      source.setAttribute("srcset", STILL_BASE + STILL_THEME + ".webp");
      var img = doc.createElement("img");
      img.className = "city-still";
      img.setAttribute("src", STILL_BASE + STILL_THEME + ".jpg");
      img.setAttribute("alt", "");
      img.setAttribute("decoding", "async");
      picture.appendChild(source);
      picture.appendChild(img);
      bg.insertBefore(picture, scrim);
      record("still", reason);
    }

    function stopLoop() {
      if (rafId !== null) caf(rafId);
      rafId = null;
    }

    function onResize() {
      if (!scene) return;
      scene.resize(win.innerWidth, win.innerHeight);
    }

    var monitor = createFpsMonitor({ onLow: function () { switchToStill("low-fps"); } });

    function frame(t) {
      rafId = null;
      if (disposed || !scene) return;
      monitor.tick(t);
      if (!scene) return; // the monitor may have swapped to the still image
      if (lastRender === null || t - lastRender >= FRAME_MS - 1) {
        var dt = lastTick === null ? 0 : Math.min((t - lastTick) / 1000, 0.1);
        try {
          scene.step(dt * CAMERA.SPEED);
          scene.render();
        } catch (e) {
          logFailure("render a frame", e);
          switchToStill("render-failed");
          return;
        }
        lastRender = t;
        lastTick = t;
      }
      rafId = raf(frame);
    }

    function onVisibility() {
      if (disposed || !scene) return;
      if (doc.visibilityState === "hidden") {
        stopLoop();
      } else if (rafId === null) {
        monitor.reset();
        lastRender = null;
        lastTick = null;
        rafId = raf(frame);
      }
    }

    function onContextLost(event) {
      event.preventDefault();
      switchToStill("context-lost");
    }

    function stopAnimation() {
      stopLoop();
      doc.removeEventListener("visibilitychange", onVisibility);
      win.removeEventListener("resize", onResize);
      if (canvas) canvas.removeEventListener("webglcontextlost", onContextLost);
      if (scene) scene.dispose();
      scene = null;
      if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
      canvas = null;
    }

    function switchToStill(reason) {
      if (disposed) return;
      stopAnimation();
      showStill(reason);
    }

    function dispose() {
      if (disposed) return;
      stopAnimation();
      disposed = true;
      if (mutationWatcher) mutationWatcher.disconnect();
      if (darkQuery && darkQuery.removeEventListener) darkQuery.removeEventListener("change", applyTheme);
      if (bg.parentNode) bg.parentNode.removeChild(bg);
      if (current === ctl) current = null;
    }

    // Follow the theme: the system setting and an explicit data-theme on <html>.
    try {
      darkQuery = win.matchMedia("(prefers-color-scheme: dark)");
      if (darkQuery.addEventListener) darkQuery.addEventListener("change", applyTheme);
    } catch (e) { darkQuery = null; }
    if (typeof win.MutationObserver === "function") {
      mutationWatcher = new win.MutationObserver(applyTheme);
      mutationWatcher.observe(doc.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    }

    current = ctl;

    // The device check. Everything here happens before any three.js download.
    if (mediaMatches(win, "(prefers-reduced-motion: reduce)")) {
      showStill("reduced-motion");
      return Promise.resolve(ctl);
    }
    if (win.innerWidth < PHONE_MAX_WIDTH && mediaMatches(win, "(pointer: coarse)")) {
      showStill("phone");
      return Promise.resolve(ctl);
    }
    if (!hasWebGL(doc)) {
      showStill("no-webgl");
      return Promise.resolve(ctl);
    }

    return Promise.resolve().then(loadThree).then(function (THREE) {
      if (disposed) return ctl;
      canvas = doc.createElement("canvas");
      canvas.className = "city-canvas";
      try {
        scene = createScene(THREE, {
          canvas: canvas,
          width: win.innerWidth,
          height: win.innerHeight,
          dpr: win.devicePixelRatio || 1,
          colors: themeColors(),
        });
      } catch (e) {
        logFailure("create the scene", e);
        canvas = null;
        showStill("no-webgl");
        return ctl;
      }
      bg.insertBefore(canvas, scrim);
      canvas.addEventListener("webglcontextlost", onContextLost);
      win.addEventListener("resize", onResize);
      doc.addEventListener("visibilitychange", onVisibility);
      record("animated");
      try {
        scene.step(0);
        scene.render();
      } catch (e) {
        logFailure("render the first frame", e);
        switchToStill("render-failed");
        return ctl;
      }
      if (doc.visibilityState !== "hidden") rafId = raf(frame);
      return ctl;
    }, function (e) {
      if (!disposed) {
        logFailure("load three.js from " + THREE_URL, e);
        showStill("load-failed");
      }
      return ctl;
    });
  }

  var api = {
    start: start,
    createScene: createScene,
    createGrid: createGrid,
    createFpsMonitor: createFpsMonitor,
    deriveColors: deriveColors,
    parseColor: parseColor,
    readTokens: readTokens,
    MAX_WINDOWS: MAX_WINDOWS,
    SPACING: SPACING,
    CAMERA: CAMERA,
    LOW_FPS: LOW_FPS,
    LOW_FPS_SECONDS: LOW_FPS_SECONDS,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    window.CityBackground = api;
    try {
      start();
    } catch (e) {
      logFailure("start", e);
    }
  }
})();
