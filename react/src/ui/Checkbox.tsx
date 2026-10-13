import type { ReactNode } from "react";
import { Checkbox as AriaCheckbox, type CheckboxProps } from "react-aria-components";
import styles from "./Checkbox.module.css";

/** A choice that's on or off, its label beside its box: ink when on, never indigo, since it changes nothing on the page. */
export function Checkbox({
  children,
  ...props
}: Omit<CheckboxProps, "children" | "className"> & { children: ReactNode }) {
  return (
    <AriaCheckbox {...props} className={styles.checkbox}>
      <svg viewBox="0 0 14 14" aria-hidden="true" className={styles.box}>
        <rect x=".5" y=".5" width="13" height="13" rx="1" />
        <path d="M3.5 7.2 6 9.6l4.5-5" />
      </svg>
      {children}
    </AriaCheckbox>
  );
}
