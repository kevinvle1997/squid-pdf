import type { ReactNode } from "react";
import styles from "./Num.module.css";

/** Tabular figures, so digits don't jitter as they change. */
export function Num({ children }: { children: ReactNode }) {
  return <span className={styles.num}>{children}</span>;
}
