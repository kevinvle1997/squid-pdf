import type { ReactNode } from "react";
import { Checkbox as AriaCheckbox, type CheckboxProps } from "react-aria-components";
import styles from "./Checkbox.module.css";

/** A choice that's on or off, its label beside its box: ink when on, never blue, since it changes nothing on the page. */
export function Checkbox({
  children,
  ...props
}: Omit<CheckboxProps, "children" | "className"> & { children: ReactNode }) {
  return (
    <AriaCheckbox {...props} className={styles.checkbox}>
      <svg viewBox="0 0 12 12" aria-hidden="true" className={styles.box}>
        <rect x=".5" y=".5" width="11" height="11" rx="3" />
        <path d="M3.2 6.2 5.2 8.2 8.8 4" />
      </svg>
      {children}
    </AriaCheckbox>
  );
}
