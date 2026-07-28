import assert from "node:assert/strict";
import test from "node:test";

import { evaluateAudit } from "./audit-dependencies.mjs";

const advisory = {
  source: 1,
  name: "react-router",
  dependency: "react-router",
  title: "RSC-only advisory",
  url: "https://github.com/advisories/GHSA-qwww-vcr4-c8h2",
  severity: "high",
};

const configuration = {
  schema_version: 1,
  exceptions: [
    {
      advisory: "GHSA-qwww-vcr4-c8h2",
      packages: ["react-router", "react-router-dom"],
      expires_on: "2026-08-28",
      reason: "Client-only application.",
      tracking: "https://example.test/tracking",
    },
  ],
};

const audit = {
  vulnerabilities: {
    "react-router": {
      severity: "high",
      via: [advisory],
    },
    "react-router-dom": {
      severity: "high",
      via: ["react-router"],
    },
  },
};

test("allows a documented direct and transitive finding before expiration", () => {
  const result = evaluateAudit(audit, configuration, new Date("2026-07-28T00:00:00Z"));

  assert.deepEqual(result.blocked, []);
  assert.deepEqual(
    result.allowed.map((finding) => finding.packageName),
    ["react-router", "react-router-dom"],
  );
});

test("blocks an exception after its expiration date", () => {
  const result = evaluateAudit(audit, configuration, new Date("2026-08-29T00:00:00Z"));

  assert.deepEqual(result.allowed, []);
  assert.deepEqual(
    result.blocked.map((finding) => finding.packageName),
    ["react-router", "react-router-dom"],
  );
});

test("blocks every unrelated high-severity advisory", () => {
  const unrelatedAudit = {
    vulnerabilities: {
      "other-package": {
        severity: "high",
        via: [
          {
            ...advisory,
            name: "other-package",
            dependency: "other-package",
            url: "https://github.com/advisories/GHSA-xxxx-yyyy-zzzz",
          },
        ],
      },
    },
  };

  const result = evaluateAudit(unrelatedAudit, configuration, new Date("2026-07-28T00:00:00Z"));

  assert.deepEqual(result.allowed, []);
  assert.deepEqual(result.blocked[0].advisories, ["GHSA-xxxx-yyyy-zzzz"]);
});

test("fails closed when npm audit omits vulnerability data", () => {
  assert.throws(
    () => evaluateAudit({ error: "registry unavailable" }, configuration),
    /must contain a vulnerabilities object/,
  );
});
