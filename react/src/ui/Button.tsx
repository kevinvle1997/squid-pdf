import type { Ref } from "react";
import { Button as AriaButton, type ButtonProps } from "react-aria-components";
import styles from "./Button.module.css";
import { classes } from "./classes";

/** In ink, never blue, since pressing it isn't a change. */
export function Button({
  variant = "secondary",
  className,
  keepFocus = false,
  ...props
}: ButtonProps & {
  variant?: "secondary" | "primary" | "quiet";
  className?: string;
  keepFocus?: boolean; // so a field being typed in keeps it
  ref?: Ref<HTMLButtonElement>;
}) {
  return (
    <AriaButton
      preventFocusOnPress={keepFocus}
      excludeFromTabOrder={keepFocus}
      {...props}
      className={classes(styles.button, styles[variant], className)}
    />
  );
}
