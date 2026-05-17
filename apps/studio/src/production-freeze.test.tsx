import { describe, expect, it } from "vitest";

import apiSource from "./api.ts?raw";
import manifest from "./test/production-freeze-manifest.json";
import typesSource from "./types.ts?raw";

function exportedNames(source: string, pattern: RegExp) {
  return Array.from(source.matchAll(pattern), (match) => match[1]);
}

function exportedSymbols(source: string) {
  const declarations = exportedNames(
    source,
    /export\s+(?:async\s+)?(?:class|const|enum|function|interface|let|type|var)\s+([A-Za-z0-9_]+)/g,
  );
  const reexports = Array.from(source.matchAll(/export\s*\{([^}]+)\}/g), (match) => match[1])
    .flatMap((group) => group.split(","))
    .map((item) => item.trim().split(/\s+as\s+/).pop() || "")
    .filter(Boolean);
  return [...declarations, ...reexports];
}

const pageModules = import.meta.glob("./pages/*.tsx", { eager: true, query: "?raw", import: "default" });

describe("production freeze perimeter", () => {
  it("keeps Studio frontend API exports inside the v1.4.11 strict freeze manifest", () => {
    const actual = exportedSymbols(apiSource);

    expect(manifest.freeze_version).toBe("v1.4.11");
    expect(manifest.strict).toBe(true);
    expect(actual).toEqual(manifest.api_symbols);
  });

  it("keeps Studio frontend type exports inside the v1.4.11 strict freeze manifest", () => {
    const actual = exportedNames(typesSource, /export\s+type\s+([A-Za-z0-9_]+)/g);

    expect(actual).toEqual(manifest.type_exports);
  });

  it("keeps Studio frontend route pages inside the v1.4.11 strict freeze manifest", () => {
    const actual = Object.keys(pageModules)
      .map((modulePath) => {
        const parts = modulePath.split("/");
        return parts[parts.length - 1] || "";
      })
      .sort();

    expect(actual).toEqual([...manifest.page_files].sort());
  });
});
