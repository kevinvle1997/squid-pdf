import type { ReactNode } from "react";
import styles from "./Notice.module.css";
import { Warn } from "./Warn";

export function Notice({ tone, children }: { tone: "plain" | "warn"; children: ReactNode }) {
  return <p className={styles.notice}>{tone === "warn" ? <Warn mark>{children}</Warn> : children}</p>;
}
