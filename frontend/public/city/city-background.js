// Night city behind the whole page: a low-poly district seen from a slow, hovering helicopter.
// Phones, reduced-motion, no WebGL and slow devices get a still image of the same view instead.
// Colors come from the CSS tokens in style.css, so the theme stays in one place.
(function (root) {
  "use strict";

  // -- Tuning: change these to taste ---------------------------------------------------------
  var ORBIT_PERIOD_S = 210;      // one full revolution around the center (3 to 4 minutes)
  var TILT_DEG = 40;             // how far the camera looks down toward the center
  var CAMERA_HEIGHT = 130;       // height above the ground, in scene units
  var DRIFT_FRACTION = 0.04;     // vertical drift, as a share of the camera height
  var DRIFT_PERIOD_S = 330;      // one up-and-down cycle; longer than the orbit so they never line up
  var FOV_DEG = 45;
  var STILL_TIME_S = 12;         // the moment of the orbit that the still image shows

  var MAX_DPR = 2;               // device pixel ratio cap
  var MAX_FPS = 30;              // render cap
  var LOW_FPS = 30;              // below this for LOW_FPS_SECONDS in a row, swap to the still image
  var LOW_FPS_SLACK = 1;         // a steady 30 Hz display can measure 29.9
  var LOW_FPS_SECONDS = 3;
  var PHONE_MAX_WIDTH = 768;     // a viewport narrower than this with a coarse pointer is a phone

  var FOG_MIX = 0.2;           // how much deep blue the fog takes on from the background
  var CENTER = { x: 0, y: 0, z: 0 };

  var THREE_URL = "/public/city/vendor/three.min.js";
  var STILL_WEBP = "/public/city/city-still.webp";
  var STILL_JPG = "/public/city/city-still.jpg";

  // Used only if a token is missing, so the scene never breaks. Named colors, not the palette.
  var DEFAULT_TOKENS = { ground: "black", building: "black", window: "cyan", orange: "orange", deep: "blue" };

  // -- Camera --------------------------------------------------------------------------------

  // Where the camera is, and what it looks at, `t` seconds in. It circles the center at a fixed
  // distance and only bobs up and down a little: no forward travel, nothing tied to scrolling.
  function cameraPose(t) {
    var tilt = (TILT_DEG * Math.PI) / 180;
    var radius = CAMERA_HEIGHT / Math.tan(tilt);
    var angle = (2 * Math.PI * t) / ORBIT_PERIOD_S;
    var drift = Math.sin((2 * Math.PI * t) / DRIFT_PERIOD_S) * DRIFT_FRACTION;
    return {
      position: {
        x: CENTER.x + radius * Math.cos(angle),
        y: CAMERA_HEIGHT * (1 + drift),
        z: CENTER.z + radius * Math.sin(angle),
      },
      target: { x: CENTER.x, y: CENTER.y, z: CENTER.z },
    };
  }

  // -- Deciding what to show ----------------------------------------------------------------

  function readEnvironment(win) {
    var matches = function (query) {
      return typeof win.matchMedia === "function" && !!win.matchMedia(query).matches;
    };
    return {
      width: win.innerWidth,
      coarse: matches("(pointer: coarse)"),
      reducedMotion: matches("(prefers-reduced-motion: reduce)"),
    };
  }

  function hasWebGL(doc) {
    try {
      var canvas = doc.createElement("canvas");
      return !!(canvas.getContext("webgl2") || canvas.getContext("webgl"));
    } catch (e) {
      return false;
    }
  }

  // env.webgl may be a function, so the context is only probed when nothing else decides it.
  function chooseMode(env) {
    if (env.reducedMotion) return { mode: "still", reason: "reduced-motion" };
    if (env.width < PHONE_MAX_WIDTH && env.coarse) return { mode: "still", reason: "phone" };
    var webgl = typeof env.webgl === "function" ? env.webgl() : env.webgl;
    if (!webgl) return { mode: "still", reason: "no-webgl" };
    return { mode: "animate", reason: "" };
  }

  // -- Colors --------------------------------------------------------------------------------

  function readTokens(win, doc) {
    var style = win.getComputedStyle(doc.documentElement);
    var read = function (name, fallback) {
      var value = style.getPropertyValue(name).trim();
      return value || fallback;
    };
    return {
      ground: read("--bg", DEFAULT_TOKENS.ground),
      building: read("--surface", DEFAULT_TOKENS.building),
      window: read("--accent", DEFAULT_TOKENS.window),
      orange: read("--hot", DEFAULT_TOKENS.orange),
      deep: read("--accent-deep", DEFAULT_TOKENS.deep),
    };
  }

  // -- The city ------------------------------------------------------------------------------

  var GRID = 29;      // blocks per side
  var SPACING = 8;    // distance between block centers

  // Small seeded generator, so the city is the same on every load and in the still image.
  function makeRandom(seed) {
    var a = seed;
    return function () {
      a = (a + 0x6d2b79f5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // Everything is instanced: one mesh of buildings and one of lit strips and signs, plus the ground.
  function buildCity(THREE, tokens) {
    var random = makeRandom(2027);
    var scene = new THREE.Scene();
    var fogColor = new THREE.Color(tokens.ground).lerp(new THREE.Color(tokens.deep), FOG_MIX);
    scene.background = fogColor;
    scene.fog = new THREE.Fog(fogColor, 170, 330);

    var blue = new THREE.Color(tokens.window);
    var orange = new THREE.Color(tokens.orange);
    var deep = new THREE.Color(tokens.deep);

    var buildings = [];
    var glows = [];   // { x, y, z, sx, sy, sz, color }
    var half = (GRID - 1) / 2;
    var halfExtent = half * SPACING;
    var r, c;

    for (r = 0; r < GRID; r++) {
      for (c = 0; c < GRID; c++) {
        var x = (c - half) * SPACING;
        var z = (r - half) * SPACING;
        if (random() < 0.07) continue; // a park or empty lot
        var dist = Math.sqrt(x * x + z * z);
        var w = 4 + random() * 2.2;
        var d = 4 + random() * 2.2;
        var tall = Math.max(0, 1 - dist / 90);
        var h = 5 + Math.pow(random(), 2.2) * 24 + tall * random() * 38;
        buildings.push({ x: x, z: z, w: w, d: d, h: h });

        var bands = h > 28 ? 3 : h > 14 ? 2 : 1;
        for (var k = 0; k < bands; k++) {
          var warm = random() < 0.06;
          glows.push({
            x: x, y: h * ((k + 1) / (bands + 1)), z: z,
            sx: w + 0.08, sy: 0.35, sz: d + 0.08,
            color: warm ? orange : blue,
          });
        }
        if (random() < 0.1) { // a sign on one side near the top
          var side = random() < 0.5 ? 1 : -1;
          glows.push({
            x: x + (side * w) / 2, y: h * 0.82, z: z,
            sx: 0.35, sy: 1.4, sz: d * 0.5,
            color: orange,
          });
        }
      }
    }
    // Street lines in the deep blue, so the grid reads from the air.
    for (var s = -half; s <= half; s += 4) {
      glows.push({ x: 0, y: 0.05, z: (s + 0.5) * SPACING, sx: halfExtent * 2, sy: 0.05, sz: 0.3, color: deep });
      glows.push({ x: (s + 0.5) * SPACING, y: 0.05, z: 0, sx: 0.3, sy: 0.05, sz: halfExtent * 2, color: deep });
    }

    var boxGeometry = new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0);
    var glowGeometry = new THREE.BoxGeometry(1, 1, 1);
    var groundGeometry = new THREE.PlaneGeometry(900, 900).rotateX(-Math.PI / 2);
    var buildingMaterial = new THREE.MeshLambertMaterial({ color: new THREE.Color(tokens.building) });
    var glowMaterial = new THREE.MeshBasicMaterial({ color: new THREE.Color("white") });
    var groundMaterial = new THREE.MeshBasicMaterial({ color: new THREE.Color(tokens.ground) });

    var dummy = new THREE.Object3D();
    var buildingMesh = new THREE.InstancedMesh(boxGeometry, buildingMaterial, buildings.length);
    buildings.forEach(function (b, i) {
      dummy.position.set(b.x, 0, b.z);
      dummy.scale.set(b.w, b.h, b.d);
      dummy.updateMatrix();
      buildingMesh.setMatrixAt(i, dummy.matrix);
    });
    buildingMesh.instanceMatrix.needsUpdate = true;
    buildingMesh.frustumCulled = false;

    var glowMesh = new THREE.InstancedMesh(glowGeometry, glowMaterial, glows.length);
    var orangeCount = 0;
    glows.forEach(function (g, i) {
      dummy.position.set(g.x, g.y, g.z);
      dummy.scale.set(g.sx, g.sy, g.sz);
      dummy.updateMatrix();
      glowMesh.setMatrixAt(i, dummy.matrix);
      glowMesh.setColorAt(i, g.color);
      if (g.color === orange) orangeCount++;
    });
    glowMesh.instanceMatrix.needsUpdate = true;
    if (glowMesh.instanceColor) glowMesh.instanceColor.needsUpdate = true;
    glowMesh.frustumCulled = false;

    var ground = new THREE.Mesh(groundGeometry, groundMaterial);

    scene.add(ground);
    scene.add(buildingMesh);
    scene.add(glowMesh);
    scene.add(new THREE.AmbientLight(new THREE.Color(tokens.window), 0.5));
    var sun = new THREE.DirectionalLight(new THREE.Color(tokens.window), 1.2);
    sun.position.set(-60, 120, 40);
    scene.add(sun);

    return {
      scene: scene,
      stats: { buildings: buildings.length, strips: glows.length, orange: orangeCount },
      dispose: function () {
        [boxGeometry, glowGeometry, groundGeometry, buildingMaterial, glowMaterial, groundMaterial].forEach(function (o) {
          o.dispose();
        });
      },
    };
  }

  // A renderer, camera and city, ready to draw any moment of the orbit.
  function createView(THREE, tokens, opts) {
    var renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      powerPreference: "low-power",
      preserveDrawingBuffer: !!opts.preserveDrawingBuffer,
      canvas: opts.canvas,
    });
    renderer.setPixelRatio(Math.min(opts.dpr || 1, MAX_DPR));
    renderer.setSize(opts.width, opts.height);
    var built = buildCity(THREE, tokens);
    var camera = new THREE.PerspectiveCamera(FOV_DEG, opts.width / opts.height, 1, 600);
    return {
      renderer: renderer,
      built: built,
      camera: camera,
      render: function (t) {
        var pose = cameraPose(t);
        camera.position.set(pose.position.x, pose.position.y, pose.position.z);
        camera.lookAt(pose.target.x, pose.target.y, pose.target.z);
        renderer.render(built.scene, camera);
      },
      resize: function (width, height) {
        renderer.setSize(width, height);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
      },
      dispose: function () {
        built.dispose();
        renderer.dispose();
        if (renderer.forceContextLoss) renderer.forceContextLoss();
      },
    };
  }

  // -- The still image -----------------------------------------------------------------------

  function showStill(container, doc, reason) {
    var picture = doc.createElement("picture");
    picture.className = "city-still";
    var source = doc.createElement("source");
    source.type = "image/webp";
    source.srcset = STILL_WEBP;
    var img = doc.createElement("img");
    img.src = STILL_JPG;
    img.alt = "";
    img.decoding = "async";
    picture.appendChild(source);
    picture.appendChild(img);
    container.appendChild(picture);
    container.dataset.cityMode = "still";
    container.dataset.cityReason = reason;
  }

  // -- Running it ----------------------------------------------------------------------------

  function defaultLoadThree() {
    return import(THREE_URL);
  }

  // Resolves to a controller { mode, reason, stop() }, or null if the page has no #cityBackground.
  function start(options) {
    var opts = options || {};
    var win = opts.win || window;
    var doc = opts.doc || document;
    var container = doc.getElementById("cityBackground");
    if (!container) return Promise.resolve(null);

    var now = opts.now || function () { return win.performance.now(); };
    var raf = opts.raf || function (cb) { return win.requestAnimationFrame(cb); };
    var caf = opts.caf || function (id) { win.cancelAnimationFrame(id); };
    var loadThree = opts.loadThree || defaultLoadThree;

    var env = readEnvironment(win);
    env.webgl = opts.hasWebGL || function () { return hasWebGL(doc); };
    var choice = chooseMode(env);
    var controller = { mode: choice.mode, reason: choice.reason, stop: function () {} };

    if (choice.mode === "still") {
      showStill(container, doc, choice.reason);
      return Promise.resolve(controller);
    }

    return Promise.resolve()
      .then(loadThree)
      .then(function (THREE) {
        var view;
        try {
          view = createView(THREE, readTokens(win, doc), {
            width: win.innerWidth, height: win.innerHeight, dpr: win.devicePixelRatio,
          });
        } catch (e) {
          controller.mode = "still";
          controller.reason = "no-webgl";
          showStill(container, doc, "no-webgl");
          return controller;
        }
        animate(view);
        return controller;
      }, function () {
        controller.mode = "still";
        controller.reason = "load-failed";
        showStill(container, doc, "load-failed");
        return controller;
      });

    function animate(view) {
      var canvas = view.renderer.domElement;
      var rafId = 0;
      var startedAt = now();
      var lastRender = -Infinity;
      var minFrameMs = 1000 / MAX_FPS - 2;
      var windowStart = null;
      var frames = 0;
      var warm = true;
      var slowSeconds = 0;

      container.appendChild(canvas);
      container.dataset.cityMode = "animate";
      container.dataset.cityReason = "";

      // Counts display frames, not renders: the render cap would otherwise read as 30 fps on a fast screen.
      function lowFrameRate(ts) {
        if (windowStart === null) { windowStart = ts; frames = 0; return false; }
        frames += 1;
        var elapsed = ts - windowStart;
        if (elapsed < 1000) return false;
        var fps = (frames * 1000) / elapsed;
        windowStart = ts;
        frames = 0;
        if (warm) { warm = false; return false; } // the first second includes shader compiling
        slowSeconds = fps < LOW_FPS - LOW_FPS_SLACK ? slowSeconds + 1 : 0;
        return slowSeconds >= LOW_FPS_SECONDS;
      }

      function tick(ts) {
        rafId = 0;
        if (lowFrameRate(ts)) { swap("low-fps"); return; }
        rafId = raf(tick);
        if (ts - lastRender >= minFrameMs) {
          lastRender = ts;
          view.render((ts - startedAt) / 1000);
        }
      }

      function pause() {
        if (rafId) caf(rafId);
        rafId = 0;
      }

      function resume() {
        if (rafId) return;
        windowStart = null;
        frames = 0;
        slowSeconds = 0;
        rafId = raf(tick);
      }

      function onVisibility() {
        if (doc.hidden) pause(); else resume();
      }
      function onResize() { view.resize(win.innerWidth, win.innerHeight); }
      function onContextLost(event) {
        if (event && event.preventDefault) event.preventDefault();
        swap("context-lost");
      }

      function teardown() {
        pause();
        doc.removeEventListener("visibilitychange", onVisibility);
        win.removeEventListener("resize", onResize);
        canvas.removeEventListener("webglcontextlost", onContextLost);
        view.dispose();
        if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
        controller.stop = function () {};
      }

      function swap(reason) {
        teardown();
        controller.mode = "still";
        controller.reason = reason;
        showStill(container, doc, reason);
      }

      doc.addEventListener("visibilitychange", onVisibility);
      win.addEventListener("resize", onResize);
      canvas.addEventListener("webglcontextlost", onContextLost);
      controller.stop = teardown;

      view.render(0);
      if (!doc.hidden) resume();
    }
  }

  var api = {
    ORBIT_PERIOD_S: ORBIT_PERIOD_S, TILT_DEG: TILT_DEG, CAMERA_HEIGHT: CAMERA_HEIGHT, DRIFT_FRACTION: DRIFT_FRACTION,
    DRIFT_PERIOD_S: DRIFT_PERIOD_S, FOV_DEG: FOV_DEG, STILL_TIME_S: STILL_TIME_S, MAX_DPR: MAX_DPR, MAX_FPS: MAX_FPS,
    LOW_FPS: LOW_FPS, LOW_FPS_SECONDS: LOW_FPS_SECONDS, PHONE_MAX_WIDTH: PHONE_MAX_WIDTH, DEFAULT_TOKENS: DEFAULT_TOKENS,
    cameraPose: cameraPose, readEnvironment: readEnvironment, hasWebGL: hasWebGL, chooseMode: chooseMode,
    readTokens: readTokens, buildCity: buildCity, createView: createView, start: start,
  };

  // The Jest tests and the still-image script reach the pieces; a browser page just starts the scene.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.CityBackground = api;
    start();
  }
})(typeof window !== "undefined" ? window : this);
