import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";

import studioLogoLight from "./assets/brand/logos/relayna-studio-light.svg";
import studioLogoDark from "./assets/brand/logos/relayna-studio-dark.svg";

type Theme = "light" | "dark";
const storageKey = "relayna.studio.theme";
const ThemeContext = createContext<{ theme: Theme; toggle: () => void } | null>(null);

function savedTheme(): Theme {
  try {
    return localStorage.getItem(storageKey) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function StudioThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(savedTheme);

  useLayoutEffect(() => {
    document.documentElement.dataset.rnTheme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      "content", theme === "dark" ? "#2C1F2E" : "#F7F0E8",
    );
  }, [theme]);

  useEffect(() => {
    function syncPreference(event: StorageEvent) {
      if (event.key !== storageKey && event.key !== null) return;
      try {
        if (event.storageArea !== localStorage) return;
      } catch {
        return;
      }
      setTheme(savedTheme());
    }
    window.addEventListener("storage", syncPreference);
    return () => window.removeEventListener("storage", syncPreference);
  }, []);

  function toggle() {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    try {
      localStorage.setItem(storageKey, next);
    } catch {
      // Keep switching available when browser storage is blocked.
    }
  }

  return <ThemeContext.Provider value={{ theme, toggle }}>{children}</ThemeContext.Provider>;
}

function useStudioTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("Studio theme controls require StudioThemeProvider.");
  return context;
}

export function StudioBrandLogo({ className }: { className?: string }) {
  const { theme } = useStudioTheme();
  return <img className={className} src={theme === "dark" ? studioLogoDark : studioLogoLight} alt="Relayna Studio" />;
}

export function StudioThemeToggle() {
  const { theme, toggle } = useStudioTheme();
  const label = `Switch to ${theme === "light" ? "dark" : "light"} mode`;
  return (
    <button type="button" className="studio-theme-toggle" onClick={toggle} aria-label={label} title={label}>
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
        {theme === "light" ? (
          <path d="M20.9 13.1A9 9 0 0 1 10.9 3.1 9 9 0 1 0 20.9 13.1Z" />
        ) : (
          <>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42" />
          </>
        )}
      </svg>
    </button>
  );
}
