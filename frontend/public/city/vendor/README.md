# Vendored three.js

`three.min.js` is three@0.186.1 (MIT license, https://github.com/mrdoob/three.js), cut down to the classes the city background uses and minified with esbuild. It is a static file, not loaded from a CDN and not part of the Worker's code.

To rebuild after changing the version in `package.json` or the classes in `scripts/three-entry.js`:

```
npm install
npm run build:three
```

Coverage tools skip this folder because it is third-party code.
