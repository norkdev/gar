import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
import { applyInitialTheme } from "./lib/theme";

// Apply the theme before the first render so every screen (Login, splash, app)
// is themed, not just the ones that mount ThemeToggle.
applyInitialTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
