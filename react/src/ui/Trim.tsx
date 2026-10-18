import type { ReactNode } from "react";
import { classes } from "./classes";
import styles from "./Trim.module.css";

/**
 * Crop marks just outside its corners, where the sheet it holds is trimmed. They hang outside the box,
 * so a sheet that clips its own overflow goes inside it, or beside an empty one in the same grid cell.
 */
export function Trim({ className, children }: { className?: string; children?: ReactNode }) {
  return <div className={classes(styles.trim, className)}>{children}</div>;
}
