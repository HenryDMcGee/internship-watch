// Builds the small three.js file the city background loads: only the classes the scene uses,
// minified, as an ES module. Run `node scripts/vendor-three.js` after changing the three version
// in package.json or the classes the scene needs, then commit the result.
//
// Output: frontend/public/vendor/three.min.mjs and LICENSE-three.txt. The extension is .mjs so the
// coverage tools (which measure frontend/**/*.js) leave third-party code alone.
const fs = require("fs");
const path = require("path");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..");
const OUT_DIR = path.join(ROOT, "frontend", "public", "vendor");

// Keep in step with what frontend/public/city-background.js uses from THREE.
const CLASSES = [
  "WebGLRenderer", "Scene", "Fog", "PerspectiveCamera", "InstancedMesh", "BoxGeometry",
  "MeshLambertMaterial", "MeshBasicMaterial", "DirectionalLight", "AmbientLight", "Object3D",
];

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const version = JSON.parse(fs.readFileSync(path.join(ROOT, "node_modules", "three", "package.json"), "utf8")).version;
  await esbuild.build({
    stdin: { contents: `export { ${CLASSES.join(", ")} } from "three";`, resolveDir: ROOT },
    bundle: true,
    format: "esm",
    minify: true,
    target: "es2020",
    legalComments: "none",
    banner: { js: `/* three.js ${version} (MIT), tree-shaken to the classes the city background uses. See LICENSE-three.txt. Rebuild: node scripts/vendor-three.js */` },
    outfile: path.join(OUT_DIR, "three.min.mjs"),
  });
  fs.copyFileSync(path.join(ROOT, "node_modules", "three", "LICENSE"), path.join(OUT_DIR, "LICENSE-three.txt"));
  const size = fs.statSync(path.join(OUT_DIR, "three.min.mjs")).size;
  console.log(`three ${version}: wrote frontend/public/vendor/three.min.mjs (${size} bytes)`);
}

main().catch((err) => { console.error(err); process.exit(1); });
