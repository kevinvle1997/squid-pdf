import type { ReactNode } from "react";
import { classes } from "./classes";
import styles from "./Trim.module.css";

/** The marks hang outside the box, so a sheet that clips its own overflow goes inside it. */
export function Trim({ className, children }: { className?: string; children?: ReactNode }) {
  return <div className={classes(styles.trim, className)}>{children}</div>;
}
