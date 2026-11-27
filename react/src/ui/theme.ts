export type Theme = "light" | "dark";

const PICKED = "squidpdf.theme";
const SYSTEM_DARK = "(prefers-color-scheme: dark)";
const listeners = new Set<() => void>();

export function themeShown(): Theme {
  const picked = document.documentElement.dataset.theme;
  if (picked === "light" || picked === "dark") return picked;
  return matchMedia(SYSTEM_DARK).matches ? "dark" : "light";
}

export function pickTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(PICKED, theme);
  for (const listener of listeners) listener();
}

export function showPickedTheme() {
  const picked = localStorage.getItem(PICKED);
  if (picked === "light" || picked === "dark") document.documentElement.dataset.theme = picked;
}

export function onThemeChange(listener: () => void) {
  const system = matchMedia(SYSTEM_DARK);
  listeners.add(listener);
  system.addEventListener("change", listener);
  return () => {
    listeners.delete(listener);
    system.removeEventListener("change", listener);
  };
}
