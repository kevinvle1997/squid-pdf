import type { ReactNode, RefObject } from "react";
import { Tooltip as AriaTooltip, type TooltipTriggerState, TooltipTriggerStateContext } from "react-aria-components";
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
