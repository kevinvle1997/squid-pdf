import { memo, useCallback, useRef, useSyncExternalStore } from "react";
import { Button, type PressEvent } from "react-aria-components";
import type { Copy, FitInfo, FontInfo, PageInfo, SpanInfo } from "../../api/types";
import { DOUBLE_PRESS_MS } from "../constants";
import { type FocusTo, focusMoved } from "../editor";
import { lookOf } from "../marks";
import type { SpanReading } from "../project";
import { edit } from "../typing";
import { useEditor } from "./context";
import { boxOf } from "./geometry";
import type { Notes, Via } from "./notes";
import styles from "./SpanMark.module.css";

interface Props {
  span: SpanInfo;
  info: PageInfo;
  edited: SpanReading | undefined; // what it reads now, if an edit changed it
  font: FontInfo | undefined;
  fit: FitInfo | undefined; // the server's verdict on its edit
  copy: Copy;
  focusTo: FocusTo | null; // focus is sent here: back from its field, or put back from the margin
  notes: Notes; // the page's one note, which this mark shows its own in
  noteId: string; // the page's note, for a screen reader to read with the mark
}

/**
 * A span of the page's text: pressable, with its fidelity on hover, focus and a single tap.
 * The page hands it what it shows, so a page's marks subscribe to nothing of the editor's and
 * mount cheaply as the page scrolls near; each redraws only when what it's handed changes.
 */
export const SpanMark = memo(function SpanMark({ span, info, edited, font, fit, copy, focusTo, notes, noteId }: Props) {
  const editor = useEditor();
  const look = lookOf({ formField: span.form_field, why: span.why, font, edited, fit, copy });
  const described = useSyncExternalStore(notes.store.subscribe, () => notes.store.get().shown?.spanId === span.id);
  const lastPress = useRef(0);
  // Sent focus, the mark takes it as it's drawn: in the field's place, or where it already stood.
  const takeFocus = useCallback(
    (mark: HTMLButtonElement | null) => {
      if (focusTo !== null) mark?.focus();
    },
    [focusTo],
  );

  // While focus is sent here, the note stays shut: it would cover the words just typed or put back.
  const showNote = (anchor: EventTarget, via: Via) => {
    if (look.note !== null && focusTo === null && anchor instanceof Element) notes.show(span.id, anchor, via);
  };

  function pressed(event: PressEvent) {
    // Enter, Space, or a screen reader's activation: edit at once, the whole span selected.
    if (event.pointerType === "keyboard" || event.pointerType === "virtual") {
      notes.hide(span.id);
      edit(editor, span.id, null);
      return;
    }
    const at = performance.now();
    if (at - lastPress.current < DOUBLE_PRESS_MS) {
      lastPress.current = 0;
      // Where the press landed, in points from the span's start: the word to select.
      const width = (event.target as HTMLElement).getBoundingClientRect().width;
      const atPt = width > 0 ? (event.x / width) * (span.bbox.x1 - span.bbox.x0) : null;
      notes.hide(span.id);
      edit(editor, span.id, atPt);
      return;
    }
    lastPress.current = at;
    showNote(event.target, "tap"); // a tap shows the note a pointer would hover for
    // Nothing hovers off on a touch screen, so the next press anywhere closes it.
    window.addEventListener("pointerdown", () => notes.hide(span.id), { once: true, capture: true });
  }

  const className = [
    styles.span,
    look.substituted && styles.substitute,
    look.approximate && styles.approximate,
    look.formField && styles.formField,
    look.changed && styles.changed,
    look.trouble && styles.trouble,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <Button
      ref={takeFocus}
      className={className}
      style={boxOf(span.bbox, info)}
      aria-describedby={described ? noteId : undefined}
      onPress={pressed}
      onHoverStart={(event) => showNote(event.target, "hover")}
      onHoverEnd={(event) => {
        notes.hide(span.id);
        // Still focused: its note stays for the keyboard, as the one focus showed.
        if (event.target === document.activeElement) showNote(event.target, "focus");
      }}
      onFocus={(event) => showNote(event.target, "focus")}
      onBlur={() => {
        notes.hide(span.id);
        if (focusTo !== null) focusMoved(editor);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") notes.hide(span.id);
        else event.continuePropagation();
      }}
    >
      <span className="vh">{edited?.text ?? span.text}</span>
    </Button>
  );
});
