import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

const guardUrl = new URL("./check-node-version.mjs", import.meta.url).href;

function runWithVersion(version, moduleUrl = guardUrl) {
  return spawnSync(process.execPath, ["--input-type=module", "--eval", `
    Object.defineProperty(process.versions, "node", { value: ${JSON.stringify(version)} });
    await import(${JSON.stringify(moduleUrl)});
    console.log("Runtime accepted");
  `], { encoding: "utf8" });
}

test("rejects vulnerable, unsupported, and prerelease runtimes before executing tooling", () => {
  for (const version of ["20.19.0", "22.23.3", "24.21.9", "24.22.0", "26.12.0", "24.22.1-rc.1"]) {
    const result = runWithVersion(version);
    assert.equal(result.status, 1, version);
    assert.match(result.stderr, /Studio requires patched Node.js/);
    assert.doesNotMatch(result.stdout, /Runtime accepted/);
  }
});

test("accepts the patched baseline and newer patches and minors in the supported major", () => {
  for (const version of ["24.22.1", "24.22.2", "24.23.0"]) {
    const result = runWithVersion(version);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /Runtime accepted/);
  }
});

test("direct dependency-audit imports reject an unpatched runtime before running the audit", () => {
  const result = runWithVersion("26.12.0", new URL("./audit-dependencies.mjs", import.meta.url).href);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Studio requires patched Node.js/);
  assert.doesNotMatch(result.stdout, /Runtime accepted/);
});
