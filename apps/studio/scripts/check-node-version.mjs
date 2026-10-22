import { readFileSync } from "node:fs";

const { engines } = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const minimum = /^\^(\d+)\.(\d+)\.(\d+)$/.exec(engines.node);
if (!minimum) {
  throw new Error("Studio's Node.js engine must specify a patched major with ^major.minor.patch.");
}

const [major, minor, patch] = minimum.slice(1).map(Number);
const [actualMajor, actualMinor, actualPatch] = process.versions.node.split(".").map(Number);
if (
  ![actualMajor, actualMinor, actualPatch].every(Number.isSafeInteger) ||
  actualMajor !== major ||
  actualMinor < minor ||
  (actualMinor === minor && actualPatch < patch)
) {
  console.error(
    `Studio requires patched Node.js ${engines.node}; found ${process.versions.node}. ` +
    "Run nvm install && nvm use from apps/studio, or install a supported Node.js version.",
  );
  process.exit(1);
}
