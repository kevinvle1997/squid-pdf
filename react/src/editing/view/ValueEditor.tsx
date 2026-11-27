import { CalendarDate, getWeeksInMonth } from "@internationalized/date";
import {
  type FocusEvent,
  type KeyboardEvent,
  type RefObject,
  useCallback,
  useContext,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useFocusVisible, useFocusWithin, useLocale } from "react-aria";
import {
  Calendar,
  CalendarCell,
  CalendarGrid,
  CalendarGridBody,
  CalendarGridHeader,
  type CalendarGridProps,
  CalendarHeaderCell,
  CalendarStateContext,
  Disclosure,
  DisclosurePanel,
  Heading,
  Input,
  NumberField,
} from "react-aria-components";
import { Button } from "../../ui/Button";
import { coarsePointer, onPointerChange } from "../../ui/pointer";
import { Shortcut } from "../../ui/Tooltip";
import { Warn } from "../../ui/Warn";
import type { AmountValue } from "../amounts";
import { isUndoOrRedo } from "../commands";
import type { DateValue } from "../dates";
import { announce, focusMoved } from "../editor";
import type { EditableValue } from "../formattedValues";
import { spanDraftOf } from "../typing";
import {
  amountFormatOptions,
  chooseAmount,
  chooseDate,
  closeValueEditor,
  markedDays,
  openValueEditor,
  reasonsAnyDayGives,
  shownDayAfter,
} from "../valueEditor";
import { useEditor, useEditorState } from "./context";
import { ALT } from "./keys";
import { useLeaveTyping } from "./leaveTyping";
import styles from "./ValueEditor.module.css";

const KIND = { date: "Date", amount: "Amount" } as const;

const MOST_WEEKS = 6;

export function ValueEditor({ value, field }: { value: EditableValue; field: RefObject<HTMLElement | null> }) {
  const editor = useEditor();
  const open = useEditorState((state) => spanDraftOf(state)?.valueEditorOpen ?? false);
  const coarse = useSyncExternalStore(onPointerChange, coarsePointer);
  const leave = useLeaveTyping();
  const { focusWithinProps } = useFocusWithin({
    onBlurWithin: (event: FocusEvent) => {
      if (event.relatedTarget === field.current) return;
      if (editor.store.get().focusTo !== null) return;
      leave(event);
    },
  });
  const controlId = useId();
  const control = (
    <Button id={controlId} slot="trigger" keepFocus className={styles.control}>
      {KIND[value.kind]}
      <Chevron open={open} />
    </Button>
  );

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    // Undo and redo are the typing's: the number field gives up its own undo for it.
    if (isUndoOrRedo(event)) {
      event.preventDefault();
      return;
    }
    if (event.key !== "Escape") return;
    event.preventDefault();
    closeValueEditor(editor);
  }

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: it hears what its controls don't handle
    <div {...focusWithinProps} className={styles.editor} onKeyDown={onKeyDown}>
      <Disclosure
        isExpanded={open}
        onExpandedChange={(expanded) => (expanded ? openValueEditor(editor, value) : closeValueEditor(editor))}
        className={styles.disclosure}
      >
        {value.kind === "date" && !coarse ? (
          <Shortcut keys={`${ALT}↓`} placement="end">
            {control}
          </Shortcut>
        ) : (
          control
        )}
        <DisclosurePanel className={styles.panel}>
          {open && <EditorFor value={value} labelledBy={controlId} />}
        </DisclosurePanel>
      </Disclosure>
    </div>
  );
}

function EditorFor({ value, labelledBy }: { value: EditableValue; labelledBy: string }) {
  switch (value.kind) {
    case "date":
      return <DateEditor value={value} labelledBy={labelledBy} />;
    case "amount":
      return <AmountEditor value={value} labelledBy={labelledBy} />;
    default:
      return value satisfies never;
  }
}

function DateEditor({ value, labelledBy }: { value: DateValue; labelledBy: string }) {
  const editor = useEditor();
  const text = useEditorState((state) => state.draft?.text ?? "");
  const { year, month, day } = value.date;
  const chosen = new CalendarDate(year, month, day);
  const [focused, setFocused] = useState(chosen);
  const [shown, setShown] = useState<number | null>(null);
  const { isFocusVisible } = useFocusVisible();
  const reasonsIn = (date: CalendarDate) =>
    markedDays(editor.store.get(), { value, text, year: date.year, month: date.month });
  const marked = reasonsIn(focused);
  const reason = shown === null ? undefined : marked.get(shown);
  const reserved = useMemo(
    () => reasonsAnyDayGives(editor.store.get(), { value, text, year: focused.year }),
    [editor, value, text, focused.year],
  );
  const { locale } = useLocale();
  const weeksShort = MOST_WEEKS - getWeeksInMonth(focused, locale);

  function focusOn(date: CalendarDate) {
    setFocused(date);
    const why = reasonsIn(date).get(date.day);
    if (isFocusVisible) setShown(why === undefined ? null : date.day);
    if (why !== undefined) announce(editor, why);
  }

  function choose(date: CalendarDate) {
    if (coarsePointer() && marked.has(date.day) && shown !== date.day) {
      setShown(date.day);
      return;
    }
    chooseDate(editor, value, { year: date.year, month: date.month, day: date.day });
  }

  return (
    <Calendar
      aria-labelledby={labelledBy}
      value={chosen}
      focusedValue={focused}
      onFocusChange={focusOn}
      onChange={choose}
      autoFocus
      className={styles.calendar}
    >
      <header className={styles.month}>
        <Button slot="previous" variant="quiet" className={styles.step}>
          <Arrow towards="previous" />
        </Button>
        <Heading className={styles.monthName} />
        <Button slot="next" variant="quiet" className={styles.step}>
          <Arrow towards="next" />
        </Button>
      </header>
      <Days className={styles.grid} weekdayStyle="narrow">
        <CalendarGridHeader>
          {(weekday) => <CalendarHeaderCell className={styles.weekday}>{weekday}</CalendarHeaderCell>}
        </CalendarGridHeader>
        <CalendarGridBody>
          {(date) => (
            <CalendarCell
              date={date}
              className={styles.day}
              data-warned={date.month === focused.month && marked.has(date.day) ? "" : undefined}
              onHoverStart={() => date.month === focused.month && marked.has(date.day) && setShown(date.day)}
              onHoverEnd={() => setShown((was) => shownDayAfter(was, { day: date.day, isShown: false }))}
            >
              {({ formattedDate, isOutsideMonth }) => (isOutsideMonth ? null : formattedDate)}
            </CalendarCell>
          )}
        </CalendarGridBody>
      </Days>
      <div className={styles.reasons} aria-hidden="true" style={{ "--weeks-short": weeksShort }}>
        {[...new Set([...reserved, ...marked.values()])].map((why) => (
          <p key={why} className={styles.reason} data-shown={why === reason ? "" : undefined}>
            <Warn mark>{why}</Warn>
          </p>
        ))}
      </div>
    </Calendar>
  );
}

/** React Aria's calendar focuses its focused day only once told it has focus. */
function Days(props: CalendarGridProps) {
  const editor = useEditor();
  const sent = useFocusSent();
  const setFocused = useContext(CalendarStateContext)?.setFocused;
  const takeFocus = useCallback(
    (grid: HTMLTableElement | null) => {
      if (!sent || grid === null || setFocused === undefined) return;
      setFocused(true);
      focusMoved(editor);
    },
    [sent, setFocused, editor],
  );
  return <CalendarGrid ref={takeFocus} {...props} />;
}

function AmountEditor({ value, labelledBy }: { value: AmountValue; labelledBy: string }) {
  const editor = useEditor();
  const input = useRef<HTMLInputElement>(null);
  const sent = useFocusSent();
  const takeFocus = useCallback(
    (element: HTMLInputElement | null) => {
      input.current = element;
      if (!sent || element === null) return;
      element.focus();
      focusMoved(editor);
    },
    [sent, editor],
  );
  return (
    <NumberField
      aria-labelledby={labelledBy}
      defaultValue={value.amount}
      formatOptions={amountFormatOptions(value)}
      autoFocus
      className={styles.amountField}
      onChange={(amount) => {
        if (Number.isNaN(amount)) return;
        chooseAmount(editor, value, { amount, leaving: document.activeElement !== input.current });
      }}
      // Enter gives the amount on key down, focus back on key up: the number field hands handlers the plain event.
      onKeyUp={(event) => {
        if (event.key === "Enter") closeValueEditor(editor);
      }}
    >
      <Input ref={takeFocus} className={styles.amount} />
    </NumberField>
  );
}

function useFocusSent(): boolean {
  return useEditorState((state) => state.focusTo !== null && "valueEditor" in state.focusTo);
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg className={styles.chevron} width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path
        d={open ? "M3 7.5 6 4.5 9 7.5" : "M3 4.5 6 7.5 9 4.5"}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Arrow({ towards }: { towards: "previous" | "next" }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d={towards === "previous" ? "M10 3.5 5.5 8l4.5 4.5" : "M6 3.5 10.5 8 6 12.5"}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
