#!/usr/bin/env node
// ByteDigger is a Python project. This npm package is a pointer: it checks
// that python3 is around, hands off to the engine if it's installed, and
// otherwise tells you how to install it.
"use strict";

const { spawnSync } = require("node:child_process");

function has(cmd) {
  const probe = process.platform === "win32" ? "where" : "which";
  return spawnSync(probe, [cmd], { stdio: "ignore" }).status === 0;
}

if (!has("python3")) {
  console.error("bytedigger needs python3 (>= 3.9) on your PATH, and none was found.");
  console.error("Install Python first: https://www.python.org/downloads/");
  process.exit(1);
}

if (has("bytedigger-engine")) {
  const r = spawnSync("bytedigger-engine", process.argv.slice(2), { stdio: "inherit" });
  process.exit(r.status ?? 1);
}

console.error("ByteDigger's engine is a Python package -- this npm package is just a pointer.");
console.error("");
console.error("Install it with pip:");
console.error("");
console.error("  pip install git+https://github.com/guy-lifshitz/bytedigger.git#subdirectory=engine_py");
console.error("");
console.error("Then re-run this command, or use `bytedigger-engine` directly.");
console.error("Docs: https://github.com/guy-lifshitz/bytedigger");
process.exit(1);
