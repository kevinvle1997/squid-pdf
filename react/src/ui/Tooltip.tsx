import type { ReactElement, ReactNode, RefObject } from "react";
import {
  Tooltip as AriaTooltip,
  TooltipTrigger,
  type TooltipTriggerState,
  TooltipTriggerStateContext,
} from "react-aria-components";
import styles from "./Tooltip.module.css";
import { Warn } from "./Warn";

interface Props {
  heading: string;
  warn?: boolean; // the heading says it won't match
  children?: ReactNode; // the detail under the heading
  triggerRef: RefObject<Element | null>; // what it's shown over
  isOpen: boolean;
  onOpenChange: (isOpen: boolean) => void;
  id: string; // its words', for what it describes to name in aria-describedby
}

/**
 * A note on hover or focus: a heading, and a detail under it. It needs no TooltipTrigger, so
 * many triggers can share one, each saying when it's theirs. React Aria's Tooltip reads its
 * state from a trigger's context even on its own, so it's given one.
 */
export function Tooltip({ heading, warn = false, children, triggerRef, isOpen, onOpenChange, id }: Props) {
  const state: TooltipTriggerState = {
    isOpen,
    shouldSkipAnimation: false,
    open: () => onOpenChange(true),
    close: () => onOpenChange(false),
  };
  return (
    <TooltipTriggerStateContext.Provider value={state}>
      <AriaTooltip
        triggerRef={triggerRef}
        isOpen={isOpen}
        onOpenChange={onOpenChange}
        className={styles.note}
        placement="top"
        offset={8}
      >
        <span id={id} className={styles.words}>
          <span className={styles.heading}>{warn ? <Warn mark>{heading}</Warn> : heading}</span>
          {children}
        </span>
      </AriaTooltip>
    </TooltipTriggerStateContext.Provider>
  );
}

/**
 * A button's keyboard shortcut, shown under it on hover or focus, as editors show theirs; or beside it
 * (`end`), where what's under it is another button to press.
 */
export function Shortcut({
  keys,
  placement = "bottom",
  children,
}: {
  keys: string;
  placement?: "bottom" | "end";
  children: ReactElement;
}) {
  return (
    <TooltipTrigger delay={SHORTCUT_DELAY_MS}>
      {children}
      <AriaTooltip className={styles.shortcut} placement={placement} offset={6}>
        <kbd>{keys}</kbd>
      </AriaTooltip>
    </TooltipTrigger>
  );
}

/**
 * The name of a control drawn as an icon alone, beside it on hover or focus, after it.
 * A touch screen shows none: React Aria opens a tooltip only on hover and focus.
 */
export function NameTip({ name, children }: { name: string; children: ReactElement }) {
  return (
    <TooltipTrigger delay={SHORTCUT_DELAY_MS}>
      {children}
      <AriaTooltip className={styles.name} placement="end" offset={8}>
        {name}
      </AriaTooltip>
    </TooltipTrigger>
  );
}

/** A note in full over what's pressed, on hover or focus: a heading, and a detail under it. */
export function Tip({ heading, detail, children }: { heading: string; detail: string; children: ReactElement }) {
  return (
    <TooltipTrigger delay={SHORTCUT_DELAY_MS}>
      {children}
      <AriaTooltip className={styles.note} placement="top" offset={8}>
        <span className={styles.words}>
          <span className={styles.heading}>{heading}</span>
          {detail}
        </span>
      </AriaTooltip>
    </TooltipTrigger>
  );
}

// Long enough that passing over the bar shows nothing; short enough that a pause on a button does.
const SHORTCUT_DELAY_MS = 600;
