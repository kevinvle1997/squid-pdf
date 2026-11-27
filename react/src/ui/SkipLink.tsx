import type { ReactNode } from "react";
import styles from "./SkipLink.module.css";

export function SkipLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <a className={styles.skip} href={`#${to}`}>
      {children}
    </a>
  );
}
