// Light / dark theme toggle. Persists to localStorage; respects the user's
// system preference on first load.

import { useEffect, useState } from "react";

type Theme = "light" | "dark";

function initialTheme(): Theme {
  const stored = localStorage.getItem("gar.theme");
  if (stored === "light" || stored === "dark") return stored;
  if (typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  root.classList.add(theme);
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => {
    const t = initialTheme();
    applyTheme(t);
    return t;
  });

  useEffect(() => {
    applyTheme(theme);
    localStorage.setItem("gar.theme", theme);
  }, [theme]);

  const next: Theme = theme === "light" ? "dark" : "light";

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={() => setTheme(next)}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
    >
      <span aria-hidden="true">{theme === "light" ? "◐" : "◑"}</span>
      <span>{theme === "light" ? "Light" : "Dark"}</span>
    </button>
  );
}
