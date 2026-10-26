import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const initializeTheme = readFileSync(resolve(process.cwd(), "../../docs/assets/theme-init.js"), "utf8");

const observers = [];
const NativeObserver = MutationObserver;

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.rnTheme;
  delete document.body.dataset.mdColorScheme;
  const script = document.createElement("script");
  script.dataset.mdScope = "..";
  vi.spyOn(document, "currentScript", "get").mockReturnValue(script);
  // Use the native observer, retaining handles so each test can dispose it.
  vi.stubGlobal("MutationObserver", class extends NativeObserver {
    constructor(callback) {
      super(callback);
      observers.push(this);
    }
  });
  window.history.replaceState({}, "", "/relayna/guide/");
});

afterEach(() => {
  observers.splice(0).forEach((observer) => observer.disconnect());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  window.history.replaceState({}, "", "/");
  delete document.documentElement.dataset.rnTheme;
  delete document.body.dataset.mdColorScheme;
});

describe("documentation appearance before styles load", () => {
  it.each([null, "invalid-json", "{}", '{"color":{"scheme":"default"}}'])("starts light without an explicit dark choice: %s", (saved) => {
    if (saved) localStorage.setItem("/relayna/.__palette", saved);
    // A dark system preference must not determine the docs default.
    const media = vi.fn(() => ({ matches: true }));
    vi.stubGlobal("matchMedia", media);
    window.eval(initializeTheme);
    expect(document.documentElement.dataset.rnTheme).toBe("light");
    expect(media).not.toHaveBeenCalled();
  });

  it("restores Material's scoped explicit dark choice and follows manual changes", async () => {
    localStorage.setItem("/relayna/.__palette", JSON.stringify({ color: { scheme: "slate" } }));
    window.eval(initializeTheme);
    expect(document.documentElement.dataset.rnTheme).toBe("dark");
    document.body.dataset.mdColorScheme = "default";
    await Promise.resolve();
    expect(document.documentElement.dataset.rnTheme).toBe("light");
    document.body.dataset.mdColorScheme = "slate";
    await Promise.resolve();
    expect(document.documentElement.dataset.rnTheme).toBe("dark");
    const nested = document.createElement("aside");
    document.body.append(nested);
    nested.dataset.mdColorScheme = "default";
    await Promise.resolve();
    expect(document.documentElement.dataset.rnTheme).toBe("dark");
    nested.remove();
  });

  it("keeps a light default and follows manual changes when preference storage is blocked", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("Blocked"); });
    window.eval(initializeTheme);
    expect(document.documentElement.dataset.rnTheme).toBe("light");
    document.body.dataset.mdColorScheme = "slate";
    await Promise.resolve();
    expect(document.documentElement.dataset.rnTheme).toBe("dark");
  });
});
