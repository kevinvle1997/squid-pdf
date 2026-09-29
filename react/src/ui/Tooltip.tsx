import type { ReactNode } from "react";
import { Tooltip as AriaTooltip } from "react-aria-components";
import styles from "./Tooltip.module.css";
import { Warn } from "./Warn";

interface Props {
  heading: string;
  warn?: boolean; // the heading says it won't match
  children?: ReactNode; // the detail under the heading
}

/** A note on hover or focus: a heading, and a detail under it. Goes inside a TooltipTrigger. */
export function Tooltip({ heading, warn = false, children }: Props) {
  return (
    <AriaTooltip className={styles.note} placement="top" offset={8}>
      <span className={styles.heading}>{warn ? <Warn mark>{heading}</Warn> : heading}</span>
      {children}
    </AriaTooltip>
  );
}
