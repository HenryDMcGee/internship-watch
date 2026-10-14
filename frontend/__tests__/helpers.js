// Loads one of the real pages into jsdom and runs its script, the way a browser would.
const fs = require("fs");
const path = require("path");

const FRONTEND = path.resolve(__dirname, "..");

function jsonResponse(body, status) {
  const code = status === undefined ? 200 : status;
  return {
    ok: code >= 200 && code < 300,
    status: code,
    json: async () => body,
  };
}

// routes: { "GET /api/x": body | (url, opts) => response-ish }. Anything unmatched is a 404.
// Keys match the full URL first, then the URL without its query string.
function makeFetch(routes) {
  const calls = [];
  const fn = jest.fn(async (url, opts) => {
    const method = ((opts && opts.method) || "GET").toUpperCase();
    calls.push({ url, method, body: opts && opts.body ? JSON.parse(opts.body) : undefined });
    let handler = routes[method + " " + url];
    if (handler === undefined) handler = routes[method + " " + String(url).split("?")[0]];
    if (handler === undefined) return jsonResponse({ detail: "not found" }, 404);
    if (typeof handler === "function") return handler(url, opts);
    return jsonResponse(handler);
  });
  fn.calls = calls;
  return fn;
}

function loadPage(htmlFile, scriptFile, fetchImpl) {
  const html = fs.readFileSync(path.join(FRONTEND, htmlFile), "utf8")
    .replace(/<script[^>]*><\/script>/g, "");
  document.documentElement.innerHTML = html.replace(/^[\s\S]*?<html[^>]*>/i, "").replace(/<\/html>[\s\S]*$/i, "");
  global.fetch = fetchImpl;
  window.fetch = fetchImpl;
  jest.resetModules();
  return require(path.join(FRONTEND, scriptFile));
}

async function flush(n) {
  const fake = typeof setTimeout.clock !== "undefined"; // jest's fake timers: real ones would never fire
  for (let i = 0; i < (n || 10); i++) {
    if (fake) await jest.advanceTimersByTimeAsync(0);
    else await new Promise((r) => setTimeout(r, 0));
  }
}

module.exports = { jsonResponse, makeFetch, loadPage, flush };
