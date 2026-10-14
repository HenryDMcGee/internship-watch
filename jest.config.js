// Suite 1: unit tests in jsdom. Hard gate: below 80% on any measure, Jest exits nonzero.
module.exports = {
  testEnvironment: "jsdom",
  roots: ["<rootDir>/frontend"],
  testMatch: ["<rootDir>/frontend/__tests__/**/*.test.js"],
  collectCoverageFrom: ["frontend/**/*.js", "!frontend/__tests__/**"],
  coverageDirectory: "coverage/jest",
  coverageReporters: ["text", "text-summary", "lcov"],
  coverageThreshold: {
    global: { statements: 80, branches: 80, functions: 80, lines: 80 },
  },
};
