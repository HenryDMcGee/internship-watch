// Suite 2: browser tests in Chromium against tests/ui/serve.py, which serves the real FastAPI app
// on the dashboard port (8787) and the public hostname's view of it on a second port (8788).
const { defineConfig, devices } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "tests/ui",
  testMatch: "**/*.spec.js",
  globalSetup: require.resolve("./tests/ui/global-setup.js"),
  // The server holds one in-memory database, so tests run one at a time.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "test-results",
  use: {
    baseURL: "http://127.0.0.1:8787",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "python tests/ui/serve.py 8787 8788",
    url: "http://127.0.0.1:8787/",
    reuseExistingServer: !process.env.CI,
    timeout: 60000,
  },
});
