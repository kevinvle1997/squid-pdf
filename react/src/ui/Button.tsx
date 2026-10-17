import type { Ref } from "react";
import { Button as AriaButton, type ButtonProps } from "react-aria-components";
import styles from "./Button.module.css";

/** The app's one button, in ink, never blue, since pressing it isn't a change. */
export function Button({
  variant = "secondary",
  className,
  ...props
}: ButtonProps & {
  variant?: "secondary" | "primary" | "quiet"; // primary: filled, the one action a screen asks for; quiet: a tool in the bar
  className?: string;
  ref?: Ref<HTMLButtonElement>;
}) {
  const classes = `${styles.button} ${styles[variant]}`;
  return <AriaButton {...props} className={className === undefined ? classes : `${classes} ${className}`} />;
}
