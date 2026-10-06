import type { Ref } from "react";
import { Button as AriaButton, type ButtonProps } from "react-aria-components";
import styles from "./Button.module.css";

/** The app's one button: ink on the ground, never indigo, since pressing it isn't a change. */
export function Button({ className, ...props }: ButtonProps & { className?: string; ref?: Ref<HTMLButtonElement> }) {
  return (
    <AriaButton {...props} className={className === undefined ? styles.button : `${styles.button} ${className}`} />
  );
}
