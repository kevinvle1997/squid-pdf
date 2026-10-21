// The interface's theme: the system's, until the user picks one, kept in this browser. The page never themes.

export type Theme = "light" | "dark";

const PICKED = "squidpdf.theme"; // localStorage's key for the theme the user picked
const SYSTEM_DARK = "(prefers-color-scheme: dark)";
const listeners = new Set<() => void>();

/** The theme on screen: the one picked, else the system's. */
export function themeShown(): Theme {
  const picked = document.documentElement.dataset.theme;
  if (picked === "light" || picked === "dark") return picked;
  return matchMedia(SYSTEM_DARK).matches ? "dark" : "light";
}

/** Shows `theme`, and keeps it for the next visit. */
export function pickTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(PICKED, theme);
  for (const listener of listeners) listener();
}

/** Puts back the theme picked on an earlier visit, before anything is drawn. */
export function showPickedTheme() {
  const picked = localStorage.getItem(PICKED);
  if (picked === "light" || picked === "dark") document.documentElement.dataset.theme = picked;
}

/** Calls `listener` when the theme on screen changes, picked or the system's; returns how to stop. */
export function onThemeChange(listener: () => void) {
  const system = matchMedia(SYSTEM_DARK);
  listeners.add(listener);
  system.addEventListener("change", listener);
  return () => {
    listeners.delete(listener);
    system.removeEventListener("change", listener);
  };
}
