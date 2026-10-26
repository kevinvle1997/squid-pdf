import type { ReactNode } from "react";
import styles from "./AfterDelay.module.css";
import { classes } from "./classes";

// Under this, what's under way shows nothing: a flash of progress reads as a stutter.
const DELAY_MS = 500;

/** Words about something under way, shown only once it lasts past a moment: hidden by CSS, so no timer is needed. */
export function AfterDelay({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <span className={classes(styles.afterDelay, className)} style={{ animationDelay: `${DELAY_MS}ms` }}>
      {children}
    </span>
  );
}
