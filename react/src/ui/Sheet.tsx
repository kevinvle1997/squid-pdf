import type { ReactNode } from "react";
import { classes } from "./classes";
import styles from "./Sheet.module.css";
import { Trim } from "./Trim";

/** A white page with crop marks, sized to the window, for a screen with no document on it yet. */
export function Sheet({ className, children }: { className?: string; children: ReactNode }) {
  return <Trim className={classes(styles.sheet, className)}>{children}</Trim>;
}
