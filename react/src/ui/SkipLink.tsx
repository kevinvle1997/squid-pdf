import type { ReactNode } from "react";
import styles from "./SkipLink.module.css";

/** The first stop for the keyboard: out of sight until focused, then straight past the chrome. */
export function SkipLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <a className={styles.skip} href={`#${to}`}>
      {children}
    </a>
  );
}
