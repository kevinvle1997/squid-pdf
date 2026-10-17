import type { ReactNode } from "react";
import styles from "./Sheet.module.css";
import { Trim } from "./Trim";

/**
 * A white page with crop marks, for a screen with no document on it yet. What's set on it reads
 * as on white in both themes; the sheet itself keeps the theme's tokens, for its edge.
 */
export function Sheet({ className, children }: { className?: string; children: ReactNode }) {
  return <Trim className={className === undefined ? styles.sheet : `${styles.sheet} ${className}`}>{children}</Trim>;
}
