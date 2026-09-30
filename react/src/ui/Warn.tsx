import type { ReactNode } from "react";
import styles from "./Warn.module.css";

/** Words that say it won't match: --warn and bold, never plum, the one place that decides it. */
export function Warn({ children, mark = false }: { children: ReactNode; mark?: boolean }) {
  return (
    <span className={styles.warn}>
      {mark && <WarnMark />}
      {children}
    </span>
  );
}

function WarnMark() {
  return (
    <svg viewBox="0 0 12 12" aria-hidden="true" className={styles.mark}>
      <path d="M6 1.5 11 10.5H1Z" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
      <path d="M6 5v2.4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <circle cx="6" cy="8.9" r=".7" fill="currentColor" />
    </svg>
  );
}
