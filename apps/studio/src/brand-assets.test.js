import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const roots = ["src/assets/brand", "../../docs/assets/brand"];
const read = (path) => readFileSync(resolve(process.cwd(), path));
const hash = (data) => createHash("sha256").update(data).digest("hex");

describe("portable Manrope brand snapshots", () => {
  it.each(roots)("retains verified artwork, CSS, font provenance and license in %s", (root) => {
    const manifest = JSON.parse(read(`${root}/manifest.json`));
    expect(manifest.version).toBe("0.4.0");
    for (const [file, expected] of Object.entries(manifest.sha256)) {
      expect(hash(read(`${root}/${file}`)), file).toBe(expected);
    }
    const provenance = JSON.parse(read(`${root}/provenance/manrope.json`));
    expect(hash(read(`${root}/fonts/Manrope-Variable.woff2`))).toBe(provenance.sha256["Manrope-Variable.woff2"]);
    expect(hash(read(`${root}/fonts/OFL.txt`))).toBe(provenance.sha256["OFL.txt"]);
    expect(readdirSync(resolve(process.cwd(), root, "fonts")).sort()).toEqual(["Manrope-Variable.woff2", "OFL.txt"]);
  });

  it("distributes Manrope's copyright and OFL with the production frontend", () => {
    const license = read("public/fonts/OFL.txt");
    expect(license.equals(read("src/assets/brand/fonts/OFL.txt"))).toBe(true);
    expect(license.toString()).toContain("SIL OPEN FONT LICENSE Version 1.1");
    expect(license.toString()).toContain("Copyright");
  });
});
