// A stand-in for three.js that records what the city asks of it. Jest runs in jsdom, which has no
// WebGL, and these tests are about what the module builds and when it loads, not about pixels.
function makeFakeThree() {
  const log = { colors: [], geometries: [], materials: [], meshes: [], renderers: [], lights: [] };

  class Color {
    constructor(value) { this.value = value; this.mixedWith = null; log.colors.push(this); }
    lerp(other, amount) { this.mixedWith = [other.value, amount]; return this; }
  }
  class Fog { constructor(color, near, far) { this.color = color; this.near = near; this.far = far; } }
  class Scene { constructor() { this.children = []; this.fog = null; this.background = null; } add(o) { this.children.push(o); } }
  class PerspectiveCamera {
    constructor(fov, aspect, near, far) {
      this.fov = fov; this.aspect = aspect; this.near = near; this.far = far;
      this.position = { x: 0, y: 0, z: 0, set(x, y, z) { this.x = x; this.y = y; this.z = z; } };
      this.target = null;
      this.projectionUpdates = 0;
    }
    lookAt(x, y, z) { this.target = { x, y, z }; }
    updateProjectionMatrix() { this.projectionUpdates += 1; }
  }
  class Disposable { constructor() { this.disposed = false; } dispose() { this.disposed = true; } }
  class BoxGeometry extends Disposable { constructor() { super(); log.geometries.push(this); } translate() { return this; } }
  class PlaneGeometry extends Disposable { constructor() { super(); log.geometries.push(this); } rotateX() { return this; } }
  class MeshBasicMaterial extends Disposable { constructor(o) { super(); this.options = o || {}; log.materials.push(this); } }
  class MeshLambertMaterial extends Disposable { constructor(o) { super(); this.options = o || {}; log.materials.push(this); } }
  class Object3D {
    constructor() {
      this.position = { set: (x, y, z) => { this.p = [x, y, z]; } };
      this.scale = { set: (x, y, z) => { this.s = [x, y, z]; } };
      this.matrix = {};
    }
    updateMatrix() {}
  }
  class Mesh {
    constructor(geometry, material) { this.geometry = geometry; this.material = material; this.position = { set() {} }; log.meshes.push(this); }
  }
  class InstancedMesh extends Mesh {
    constructor(geometry, material, count) {
      super(geometry, material);
      this.count = count; this.matrices = 0; this.colors = [];
      this.instanceMatrix = { needsUpdate: false };
      this.instanceColor = null;
    }
    setMatrixAt() { this.matrices += 1; }
    setColorAt(i, c) { this.colors[i] = c; this.instanceColor = { needsUpdate: false }; }
  }
  class AmbientLight { constructor(color, intensity) { this.color = color; this.intensity = intensity; log.lights.push(this); } }
  class DirectionalLight extends AmbientLight { constructor(c, i) { super(c, i); this.position = { set() {} }; } }
  class WebGLRenderer extends Disposable {
    constructor(options) {
      super();
      this.options = options || {};
      this.domElement = document.createElement("canvas");
      this.pixelRatio = 1; this.size = null; this.renders = []; this.lost = false;
      log.renderers.push(this);
    }
    setPixelRatio(r) { this.pixelRatio = r; }
    setSize(w, h) { this.size = [w, h]; }
    setClearColor() {}
    render(scene, camera) { this.renders.push({ x: camera.position.x, y: camera.position.y, z: camera.position.z, target: camera.target }); }
    forceContextLoss() { this.lost = true; }
  }

  const THREE = {
    Color, Fog, Scene, PerspectiveCamera, BoxGeometry, PlaneGeometry, MeshBasicMaterial, MeshLambertMaterial,
    Object3D, Mesh, InstancedMesh, AmbientLight, DirectionalLight, WebGLRenderer,
  };
  return { THREE, log };
}

module.exports = { makeFakeThree };
