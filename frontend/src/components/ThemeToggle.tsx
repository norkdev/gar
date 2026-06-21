// Light / dark theme toggle. The theme is applied app-wide at startup
// (lib/theme); this just switches and persists it.

import { useEffect, useState } from "react";
import { applyTheme, initialTheme, type Theme } from "../lib/theme";

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    applyTheme(theme);
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
