import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const BLOCKING_SEVERITIES = new Set(["high", "critical"]);
const ADVISORY_URL_PREFIX = "https://github.com/advisories/";
const EXCEPTIONS_URL = new URL("../security-audit-exceptions.json", import.meta.url);

function advisoryFromVia(via) {
  if (typeof via !== "object" || via === null || typeof via.url !== "string") {
    return null;
  }
  return via.url.startsWith(ADVISORY_URL_PREFIX) ? via.url.slice(ADVISORY_URL_PREFIX.length) : null;
}

function collectAdvisories(vulnerabilities, packageName, visiting = new Set()) {
  if (visiting.has(packageName)) {
    throw new Error(`Dependency audit contains a cycle through ${packageName}.`);
  }
  const vulnerability = vulnerabilities[packageName];
  if (!vulnerability) {
    return new Set();
  }

  const nextVisiting = new Set(visiting);
  nextVisiting.add(packageName);
  const advisories = new Set();
  for (const via of vulnerability.via ?? []) {
    if (typeof via === "string") {
      for (const advisory of collectAdvisories(vulnerabilities, via, nextVisiting)) {
        advisories.add(advisory);
      }
      continue;
    }
    const advisory = advisoryFromVia(via);
    if (advisory) {
      advisories.add(advisory);
    }
  }
  return advisories;
}

function activeExceptionMap(configuration, now) {
  if (configuration.schema_version !== 1 || !Array.isArray(configuration.exceptions)) {
    throw new Error("security-audit-exceptions.json must use schema_version 1 with an exceptions array.");
  }

  const exceptions = new Map();
  for (const exception of configuration.exceptions) {
    if (
      typeof exception.advisory !== "string" ||
      !Array.isArray(exception.packages) ||
      typeof exception.expires_on !== "string" ||
      typeof exception.reason !== "string" ||
      typeof exception.tracking !== "string"
    ) {
      throw new Error("Every dependency audit exception must include advisory, packages, expires_on, reason, and tracking.");
    }
    if (exceptions.has(exception.advisory)) {
      throw new Error(`Duplicate dependency audit exception: ${exception.advisory}`);
    }

    const expiresAt = Date.parse(`${exception.expires_on}T23:59:59.999Z`);
    if (Number.isNaN(expiresAt)) {
      throw new Error(`Invalid expiration date for ${exception.advisory}: ${exception.expires_on}`);
    }
    exceptions.set(exception.advisory, {
      ...exception,
      expired: now.getTime() > expiresAt,
    });
  }
  return exceptions;
}

export function evaluateAudit(audit, configuration, now = new Date()) {
  if (
    typeof audit.vulnerabilities !== "object" ||
    audit.vulnerabilities === null ||
    Array.isArray(audit.vulnerabilities)
  ) {
    throw new Error("npm audit JSON must contain a vulnerabilities object.");
  }
  const vulnerabilities = audit.vulnerabilities;
  const exceptions = activeExceptionMap(configuration, now);
  const allowed = [];
  const blocked = [];

  for (const [packageName, vulnerability] of Object.entries(vulnerabilities)) {
    if (!BLOCKING_SEVERITIES.has(vulnerability.severity)) {
      continue;
    }
    const advisories = [...collectAdvisories(vulnerabilities, packageName)].sort();
    const matchingExceptions = advisories.map((advisory) => exceptions.get(advisory));
    const isAllowed =
      advisories.length > 0 &&
      matchingExceptions.every(
        (exception) => exception && !exception.expired && exception.packages.includes(packageName),
      );
    const finding = {
      packageName,
      severity: vulnerability.severity,
      advisories,
    };
    (isAllowed ? allowed : blocked).push(finding);
  }
  return { allowed, blocked };
}

async function main() {
  const configuration = JSON.parse(await readFile(EXCEPTIONS_URL, "utf8"));
  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const auditProcess = spawnSync(npmCommand, ["audit", "--audit-level=high", "--json"], {
    cwd: fileURLToPath(new URL("..", import.meta.url)),
    encoding: "utf8",
  });
  if (auditProcess.error) {
    throw auditProcess.error;
  }
  if (auditProcess.status !== 0 && auditProcess.status !== 1) {
    process.stderr.write(auditProcess.stderr);
    throw new Error(`npm audit exited unexpectedly with status ${auditProcess.status}.`);
  }

  let audit;
  try {
    audit = JSON.parse(auditProcess.stdout);
  } catch (error) {
    process.stderr.write(auditProcess.stderr);
    throw new Error(`npm audit did not return valid JSON: ${error.message}`);
  }
  if (audit.error) {
    throw new Error(`npm audit failed: ${JSON.stringify(audit.error)}`);
  }

  const { allowed, blocked } = evaluateAudit(audit, configuration);
  for (const finding of allowed) {
    console.log(
      `Allowed documented dependency finding: ${finding.packageName} (${finding.advisories.join(", ")})`,
    );
  }
  if (blocked.length > 0) {
    console.error("Unexcepted high or critical dependency vulnerabilities:");
    for (const finding of blocked) {
      console.error(
        `- ${finding.packageName} [${finding.severity}]: ${finding.advisories.join(", ") || "unknown advisory"}`,
      );
    }
    process.exitCode = 1;
    return;
  }
  console.log("No unexcepted high or critical dependency vulnerabilities found.");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
