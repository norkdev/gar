// Theme (light/dark) applied to <html> as a class. Applied once at startup
// (applyInitialTheme in main.tsx) so every screen — including Login and the
// loading splash, which don't mount ThemeToggle — respects the stored/system
// theme. ThemeToggle reuses these to switch and persist it.

export type Theme = "light" | "dark";

const STORAGE_KEY = "gar.theme";

export function initialTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  if (typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  root.classList.add(theme);
  localStorage.setItem(STORAGE_KEY, theme);
}

/** Apply the stored/system theme. Call once before the first render. */
export function applyInitialTheme(): void {
  applyTheme(initialTheme());
}
