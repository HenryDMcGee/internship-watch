// Starts every run with an empty coverage folder, so the nyc gate measures this run alone.
const fs = require("fs");
const path = require("path");

module.exports = async () => {
  fs.rmSync(path.resolve(__dirname, "..", "..", ".nyc_output"), { recursive: true, force: true });
};
