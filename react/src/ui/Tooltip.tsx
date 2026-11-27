import { type ReactElement, type ReactNode, type RefObject, useEffect, useEffectEvent } from "react";
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
  warn?: boolean;
  children?: ReactNode;
  triggerRef: RefObject<Element | null>;
  isOpen: boolean;
  onOpenChange: (isOpen: boolean) => void;
  onHoverChange?: (isHovered: boolean) => void;
  id: string;
}

/** React Aria's Tooltip reads its state from a trigger's context even on its own, so it's given one. */
export function Tooltip({
  heading,
  warn = false,
  children,
  triggerRef,
  isOpen,
  onOpenChange,
  onHoverChange,
  id,
}: Props) {
  const state: TooltipTriggerState = {
    isOpen,
    shouldSkipAnimation: false,
    open: () => (onHoverChange ?? onOpenChange)(true),
    // React Aria closes it at once when what it's over scrolls.
    close: (immediate) => (immediate || onHoverChange === undefined ? onOpenChange(false) : onHoverChange(false)),
  };
  const closeOnEscape = useEffectEvent((event: KeyboardEvent) => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    onOpenChange(false);
  });
  useEffect(() => {
    if (!isOpen) return;
    document.addEventListener("keydown", closeOnEscape, true);
    return () => document.removeEventListener("keydown", closeOnEscape, true);
  }, [isOpen]);
  return (
    <TooltipTriggerStateContext.Provider value={state}>
      <AriaTooltip
        triggerRef={triggerRef}
        isOpen={isOpen}
        onOpenChange={onOpenChange}
        className={styles.shared}
        placement="top"
        offset={NOTE_OFFSET_PX}
        style={{ "--note-gap": `${NOTE_OFFSET_PX}px` }}
        // It covers the text beside what it's on, so a press on it closes it.
        onPointerDown={() => onOpenChange(false)}
        containerPadding={GUTTER_PX}
      >
        <span id={id} className={styles.words}>
          <span className={styles.heading}>{warn ? <Warn mark>{heading}</Warn> : heading}</span>
          {children}
        </span>
      </AriaTooltip>
    </TooltipTriggerStateContext.Provider>
  );
}

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

/** A touch screen shows none: React Aria opens a tooltip only on hover and focus. */
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

export function Tip({
  heading,
  detail,
  changed = false,
  children,
}: {
  heading: string;
  detail: string;
  changed?: boolean;
  children: ReactElement;
}) {
  return (
    <TooltipTrigger delay={SHORTCUT_DELAY_MS}>
      {children}
      <AriaTooltip className={styles.note} placement="top" offset={8}>
        <span className={styles.words}>
          <span className={styles.heading}>{heading}</span>
          {changed && <BecameMark />}
          {detail}
        </span>
      </AriaTooltip>
    </TooltipTrigger>
  );
}

function BecameMark() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={styles.became}
    >
      <path d="M6 1.5v9M2.5 7 6 10.5 9.5 7" />
    </svg>
  );
}

const RING_WIDTH_PX = 2;
const RING_OFFSET_PX = 1;

// Just clear of that ring, which it would cover (WCAG 1.4.13, Hoverable).
const NOTE_OFFSET_PX = RING_WIDTH_PX + RING_OFFSET_PX;

// --s4, the gutter text keeps from the window's edge.
const GUTTER_PX = 16;

// Long enough that passing over the bar shows nothing; short enough that a pause on a button does.
const SHORTCUT_DELAY_MS = 600;
