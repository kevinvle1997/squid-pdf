import type { ReactNode } from "react";
import styles from "./Notice.module.css";
import { Warn } from "./Warn";

/** A line under the bar about what just happened: plain, or a warning. Never a toast. */
export function Notice({ tone, children }: { tone: "plain" | "warn"; children: ReactNode }) {
  return <p className={styles.notice}>{tone === "warn" ? <Warn>{children}</Warn> : children}</p>;
}
