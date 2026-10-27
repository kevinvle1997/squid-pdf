// A date's or an amount's editor in the slip, offered beside the field, never instead of it (Rule 5): what's
// chosen is written into the field, and before the pick each day the line can't take says why (Rule 1).
import { CalendarDate, getWeeksInMonth } from "@internationalized/date";
import {
  type FocusEvent,
  type KeyboardEvent,
  type RefObject,
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
  CalendarHeaderCell,
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
import type { DateValue } from "../dates";
import { announce } from "../editor";
import type { EditableValue } from "../formattedValues";
import { finish, spanDraftOf } from "../typing";
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
import styles from "./ValueEditor.module.css";

// The control names the value's kind: the reader's word for it.
const KIND = { date: "Date", amount: "Amount" } as const;

// The weeks a month's days can span, in any calendar the reader's language starts its week in.
const MOST_WEEKS = 6;

/** The value's control in the slip, and its editor under it while open. */
export function ValueEditor({ value, field }: { value: EditableValue; field: RefObject<HTMLInputElement | null> }) {
  const editor = useEditor();
  const open = useEditorState((state) => spanDraftOf(state)?.valueEditorOpen ?? false);
  const coarse = useSyncExternalStore(onPointerChange, coarsePointer);
  // Leaving the editor for anywhere but the field ends the typing, as leaving the field does.
  const { focusWithinProps } = useFocusWithin({
    onBlurWithin: (event: FocusEvent) => {
      if (event.relatedTarget === field.current) return;
      // On its way back to the field, from a choice or Escape.
      if (editor.store.get().focusTo !== null) return;
      if (document.hasFocus()) finish(editor, true);
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
    if (event.key !== "Escape") return;
    event.preventDefault();
    closeValueEditor(editor);
  }

  return (
    // Escape, from the calendar or the number field, closes it: their other keys are their own.
    // biome-ignore lint/a11y/noStaticElementInteractions: it hears what its controls don't handle
    <div {...focusWithinProps} className={styles.editor} onKeyDown={onKeyDown}>
      <Disclosure
        isExpanded={open}
        onExpandedChange={(expanded) => (expanded ? openValueEditor(editor, value) : closeValueEditor(editor))}
        className={styles.disclosure}
      >
        {/* The key is a date's, as a picker's is; a touch screen has none to hint at. */}
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

/** The editor made for the value's kind, named by its control. */
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

/** The calendar, on the date's own day: each day whose date the line can't take marked, with why. */
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
  // Every reason a day can give, laid out in the reasons' row, so stepping months never changes the slip's height.
  const reserved = useMemo(
    () => reasonsAnyDayGives(editor.store.get(), { value, text, year: focused.year }),
    [editor, value, text, focused.year],
  );
  const { locale } = useLocale();
  // The month's weeks fill six rows, as many as a month can take, so its height doesn't change with its weeks.
  const weeksShort = MOST_WEEKS - getWeeksInMonth(focused, locale);

  function focusOn(date: CalendarDate) {
    setFocused(date);
    const why = reasonsIn(date).get(date.day);
    // A key's focus shows the day's reason; a press's leaves it to the press, which comes after.
    if (isFocusVisible) setShown(why === undefined ? null : date.day);
    if (why !== undefined) announce(editor, why);
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
      </CalendarGrid>
      {/* A row of its own under the month: it covers no day and no button of the slip's. Every reason a day can
          give is laid out in it, one shown, so it's as tall as the longest and nothing moves as one shows or the
          month changes. Heard as focus reaches the day, so not read again. */}
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

/** A number field read the reader's way; what it writes keeps the page's. */
function AmountEditor({ value, labelledBy }: { value: AmountValue; labelledBy: string }) {
  const editor = useEditor();
  const input = useRef<HTMLInputElement>(null);
  return (
    <NumberField
      aria-labelledby={labelledBy}
      defaultValue={value.amount}
      formatOptions={amountFormatOptions(value)}
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
