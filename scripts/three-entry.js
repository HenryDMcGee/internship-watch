// The only parts of three.js the city uses. `npm run build:three` bundles just these into
// frontend/public/city/vendor/three.min.js, so the file the browser downloads stays small.
export {
  Scene, Fog, Color, PerspectiveCamera, BoxGeometry, PlaneGeometry, MeshBasicMaterial, MeshLambertMaterial,
  Object3D, Mesh, InstancedMesh, AmbientLight, DirectionalLight, WebGLRenderer,
} from "three";
