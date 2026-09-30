import type { ReactNode } from "react";
import styles from "./Num.module.css";

/** A measurement or a count in running text: monospaced, so digits don't jitter as they change. */
export function Num({ children }: { children: ReactNode }) {
  return <span className={styles.num}>{children}</span>;
}
