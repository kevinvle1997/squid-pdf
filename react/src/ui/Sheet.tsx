import type { ReactNode } from "react";
import { classes } from "./classes";
import styles from "./Sheet.module.css";

export function Sheet({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={classes(styles.sheet, className)}>
      <div className={styles.zone}>{children}</div>
    </div>
  );
}
