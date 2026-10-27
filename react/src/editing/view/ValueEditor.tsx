// A date's or an amount's editor in the slip, offered beside the field, never instead of it (Rule 5): what's
// chosen is written into the field, and before the pick each day the line can't take says why (Rule 1).
import { CalendarDate } from "@internationalized/date";
import {
  type FocusEvent,
  type KeyboardEvent,
  type RefObject,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useFocusVisible } from "react-aria";
import {
  Calendar,
  CalendarCell,
  CalendarGrid,
  CalendarGridBody,
  CalendarGridHeader,
  CalendarHeaderCell,
  Heading,
  Input,
  NumberField,
} from "react-aria-components";
import { Button } from "../../ui/Button";
import { coarsePointer, onPointerChange } from "../../ui/pointer";
import { Shortcut, Tooltip } from "../../ui/Tooltip";
import { announce } from "../editor";
import type { AmountValue, DateValue, EditableValue } from "../formattedValues";
import { finish } from "../typing";
import { chooseAmount, chooseDate, closeValueEditor, markedDays, openValueEditor } from "../valueEditor";
import { useEditor, useEditorState } from "./context";
import { ALT } from "./keys";
import styles from "./ValueEditor.module.css";

// The control names the value's kind: the reader's word for it.
const KIND = { date: "Date", amount: "Amount" } as const;

/** The value's control in the slip, and its editor under it while open. */
export function ValueEditor({ value, field }: { value: EditableValue; field: RefObject<HTMLInputElement | null> }) {
  const editor = useEditor();
  const open = useEditorState((state) => state.draft?.valueEditorOpen ?? false);
  const coarse = useSyncExternalStore(onPointerChange, coarsePointer);
  const controlId = useId();
  const control = (
    <Button
      id={controlId}
      keepFocus
      aria-expanded={open}
      className={styles.control}
      onPress={() => (open ? closeValueEditor(editor) : openValueEditor(editor, value))}
    >
      {KIND[value.kind]}
      <Chevron open={open} />
    </Button>
  );

  // Leaving the editor for anywhere but the field ends the typing, as leaving the field does.
  function leave(event: FocusEvent<HTMLDivElement>) {
    const to = event.relatedTarget;
    if (to instanceof Node && (event.currentTarget.contains(to) || to === field.current)) return;
    // On its way back to the field, from a choice or Escape.
    if (editor.store.get().focusTo !== null) return;
    if (document.hasFocus()) finish(editor, true);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Escape") return;
    event.preventDefault();
    closeValueEditor(editor);
  }

  return (
    // The keys are the calendar's and the number field's own; Escape, from either, closes it.
    // biome-ignore lint/a11y/noStaticElementInteractions: it hears what its controls don't handle
    <div className={styles.editor} onBlur={leave} onKeyDown={onKeyDown}>
      {/* A touch screen has no keys to hint at. */}
      {coarse ? (
        control
      ) : (
        <Shortcut keys={`${ALT}↓`} placement="end">
          {control}
        </Shortcut>
      )}
      {open && value.kind === "date" && <DateEditor value={value} labelledBy={controlId} />}
      {open && value.kind === "amount" && <AmountEditor value={value} labelledBy={controlId} />}
    </div>
  );
}

/** The calendar, on the date's own day: each day whose date the line can't take marked, with why. */
function DateEditor({ value, labelledBy }: { value: DateValue; labelledBy: string }) {
  const editor = useEditor();
  const state = useEditorState((current) => current);
  const { year, month, day } = value.date;
  const chosen = new CalendarDate(year, month, day);
  const [focused, setFocused] = useState(chosen);
  const [shown, setShown] = useState<number | null>(null);
  const { isFocusVisible } = useFocusVisible();
  const text = state.draft?.text ?? "";
  const reasonsIn = (date: CalendarDate) =>
    markedDays(editor.store.get(), { value, text, year: date.year, month: date.month });
  const marked = markedDays(state, { value, text, year: focused.year, month: focused.month });

  function focusOn(date: CalendarDate) {
    setFocused(date);
    const reason = reasonsIn(date).get(date.day);
    // A key's focus shows the day's reason; a press's leaves it to the press, which comes after.
    if (isFocusVisible) setShown(reason === undefined ? null : date.day);
    if (reason !== undefined) announce(editor, reason);
  }

  function choose(date: CalendarDate) {
    // A finger has no hover: its first tap on a marked day shows why, and a second chooses it.
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
      <CalendarGrid className={styles.grid} weekdayStyle="narrow">
        <CalendarGridHeader>
          {(weekday) => <CalendarHeaderCell className={styles.weekday}>{weekday}</CalendarHeaderCell>}
        </CalendarGridHeader>
        <CalendarGridBody>
          {(date) => (
            <Day
              date={date}
              reason={date.month === focused.month ? marked.get(date.day) : undefined}
              shown={shown === date.day && date.month === focused.month}
              onShow={(isShown) => setShown((was) => shownAfter(was, { day: date.day, isShown }))}
            />
          )}
        </CalendarGridBody>
      </CalendarGrid>
    </Calendar>
  );
}

/** The marked day whose reason shows, once `day`'s is shown or hidden. */
function shownAfter(was: number | null, { day, isShown }: { day: number; isShown: boolean }): number | null {
  if (isShown) return day;
  return was === day ? null : was;
}

/** One day of the month, and, where the line can't take its date, the warning's dashed line and why. */
function Day({
  date,
  reason,
  shown,
  onShow,
}: {
  date: CalendarDate;
  reason: string | undefined;
  shown: boolean;
  onShow: (shown: boolean) => void;
}) {
  const cell = useRef<HTMLTableCellElement>(null);
  const id = useId();
  return (
    <CalendarCell
      ref={cell}
      date={date}
      className={reason === undefined ? styles.day : styles.markedDay}
      onHoverStart={() => reason !== undefined && onShow(true)}
      onHoverEnd={() => onShow(false)}
    >
      {({ formattedDate, isOutsideMonth }) =>
        isOutsideMonth ? null : (
          <>
            {formattedDate}
            {reason !== undefined && (
              <Tooltip heading={reason} warn triggerRef={cell} isOpen={shown} onOpenChange={onShow} id={id} />
            )}
          </>
        )
      }
    </CalendarCell>
  );
}

/** A number field read the reader's way; what it writes keeps the page's. */
function AmountEditor({ value, labelledBy }: { value: AmountValue; labelledBy: string }) {
  const editor = useEditor();
  const input = useRef<HTMLInputElement>(null);
  const { code, decimals } = value.format;
  const formatOptions: Intl.NumberFormatOptions = {
    ...(code === undefined ? { style: "decimal" } : { style: "currency", currency: code }),
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  };
  return (
    <NumberField
      aria-labelledby={labelledBy}
      defaultValue={value.amount}
      formatOptions={formatOptions}
      autoFocus
      className={styles.amountField}
      onChange={(amount) => {
        // Emptied: nothing to write, and the page's amount stays.
        if (Number.isNaN(amount)) return;
        chooseAmount(editor, value, { amount, leaving: document.activeElement !== input.current });
      }}
      // Enter gives the amount as it goes down, if it changed; as it comes up, focus goes back to the field
      // either way. The number field hands its own handlers the plain event, which goes on as it is.
      onKeyUp={(event) => {
        if (event.key === "Enter") closeValueEditor(editor);
      }}
    >
      <Input ref={input} className={styles.amount} />
    </NumberField>
  );
}

/** Which way the control's editor goes: down to open it, up to close it. */
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

/** The calendar's step to the month before or after. */
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
