import { useSyncExternalStore } from "react";
import { ToggleButton } from "react-aria-components";
import styles from "./ThemeToggle.module.css";
import { onThemeChange, pickTheme, themeShown } from "./theme";

/** Switches the interface between light and dark: a moon while it's light, a sun while it's dark. */
export function ThemeToggle() {
  const dark = useSyncExternalStore(onThemeChange, themeShown) === "dark";
  return (
    <ToggleButton
      aria-label="Dark theme"
      isSelected={dark}
      onChange={(selected) => pickTheme(selected ? "dark" : "light")}
      className={styles.toggle}
    >
      <svg viewBox="0 0 16 16" aria-hidden="true" className={styles.icon}>
        {dark ? <Sun /> : <Moon />}
      </svg>
    </ToggleButton>
  );
}

function Sun() {
  return (
    <g fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round">
      <circle cx="8" cy="8" r="3" />
      <path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1.06 1.06M11.54 11.54l1.06 1.06M3.4 12.6l1.06-1.06M11.54 4.46l1.06-1.06" />
    </g>
  );
}

function Moon() {
  return (
    <path
      d="M13.5 9.6A5.75 5.75 0 0 1 6.4 2.5a5.75 5.75 0 1 0 7.1 7.1Z"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      strokeLinejoin="round"
    />
  );
}
