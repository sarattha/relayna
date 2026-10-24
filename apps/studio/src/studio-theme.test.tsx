import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StudioBrandLogo, StudioThemeProvider, StudioThemeToggle } from "./studio-theme";

const key = "relayna.studio.theme";

function Controls() {
  return <StudioThemeProvider><StudioBrandLogo /><StudioThemeToggle /></StudioThemeProvider>;
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.dataset.rnTheme = "light";
  const meta = document.createElement("meta");
  meta.name = "theme-color";
  document.head.append(meta);
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  document.documentElement.dataset.rnTheme = "light";
  document.querySelector('meta[name="theme-color"]')?.remove();
});

describe("Studio appearance", () => {
  it.each([null, "invalid", "system", "light"])("defaults to light for preference %s", (saved) => {
    if (saved !== null) localStorage.setItem(key, saved);
    render(<Controls />);
    expect(document.documentElement).toHaveAttribute("data-rn-theme", "light");
    expect(screen.getByRole("button", { name: "Switch to dark mode" })).toHaveAttribute("type", "button");
    expect(screen.getByRole("img", { name: "Relayna Studio" })).toHaveAttribute("src", expect.stringContaining("studio-light.svg"));
  });

  it("switches the theme, logo and browser colors and restores the preference after remount", () => {
    const view = render(<Controls />);
    fireEvent.click(screen.getByRole("button", { name: "Switch to dark mode" }));
    expect(document.documentElement).toHaveAttribute("data-rn-theme", "dark");
    expect(localStorage.getItem(key)).toBe("dark");
    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute("content", "#2C1F2E");
    expect(screen.getByRole("img", { name: "Relayna Studio" })).toHaveAttribute("src", expect.stringContaining("studio-dark.svg"));
    view.unmount();
    render(<Controls />);
    fireEvent.click(screen.getByRole("button", { name: "Switch to light mode" }));
    expect(document.documentElement).toHaveAttribute("data-rn-theme", "light");
    expect(localStorage.getItem(key)).toBe("light");
    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute("content", "#F7F0E8");
  });

  it("syncs a choice or cleared preference from another tab and ignores unrelated storage", () => {
    render(<Controls />);
    const sync = (eventKey: string | null, storageArea = localStorage) => act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: eventKey, storageArea }));
    });
    localStorage.setItem(key, "dark");
    sync("unrelated");
    sync(key, sessionStorage);
    expect(document.documentElement).toHaveAttribute("data-rn-theme", "light");
    sync(key);
    expect(document.documentElement).toHaveAttribute("data-rn-theme", "dark");
    localStorage.clear();
    sync(null);
    expect(document.documentElement).toHaveAttribute("data-rn-theme", "light");
  });

  it.each(["read", "write"])("keeps switching available when storage %s is blocked", (failure) => {
    vi.spyOn(Storage.prototype, failure === "read" ? "getItem" : "setItem").mockImplementation(() => {
      throw new DOMException("Storage blocked", "SecurityError");
    });
    render(<Controls />);
    fireEvent.click(screen.getByRole("button", { name: "Switch to dark mode" }));
    expect(document.documentElement).toHaveAttribute("data-rn-theme", "dark");
    fireEvent.click(screen.getByRole("button", { name: "Switch to light mode" }));
    expect(document.documentElement).toHaveAttribute("data-rn-theme", "light");
  });
});
