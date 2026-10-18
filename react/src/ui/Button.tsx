import type { Ref } from "react";
import { Button as AriaButton, type ButtonProps } from "react-aria-components";
import styles from "./Button.module.css";
import { classes } from "./classes";

/** The interface's button, in one of three variants, in ink, never blue, since pressing it isn't a change. */
export function Button({
  variant = "secondary",
  className,
  keepFocus = false,
  ...props
}: ButtonProps & {
  variant?: "secondary" | "primary" | "quiet"; // primary: filled, the one action a screen asks for; quiet: a tool in the bar
  className?: string;
  keepFocus?: boolean; // a press leaves focus where it is, so a field being typed in keeps it
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
