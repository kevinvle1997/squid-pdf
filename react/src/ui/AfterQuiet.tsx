import type { ReactNode } from "react";
import styles from "./AfterQuiet.module.css";
import { classes } from "./classes";

// Under this, what's under way shows nothing: a flash of progress reads as a stutter.
const QUIET_MS = 500;

/** Words about something under way, shown only once it lasts past a moment: hidden by CSS, so no timer is needed. */
export function AfterQuiet({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <span className={classes(styles.afterQuiet, className)} style={{ animationDelay: `${QUIET_MS}ms` }}>
      {children}
    </span>
  );
}
