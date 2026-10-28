import { memo, useCallback, useRef, useSyncExternalStore } from "react";
import { Button, type PressEvent } from "react-aria-components";
import type { FitInfo, FontInfo, PageInfo, RedactionInfo, Sentences, SpanInfo } from "../../api/types";
import { classes } from "../../ui/classes";
import { type Press, pressOf } from "../caret";
import { isShortcutPress, onEscape, passShortcut } from "../commands";
import { DOUBLE_TAP_MS } from "../constants";
import { type FocusSpan, focusMoved } from "../editor";
import { lookOf } from "../marks";
import type { SpanReading } from "../project";
import { edit } from "../typing";
import { useEditor } from "./context";
import { boxOf, markBoxOf } from "./geometry";
import type { Notes, Via } from "./notes";
import styles from "./SpanMark.module.css";

interface Props {
  span: SpanInfo;
  info: PageInfo;
  reading: SpanReading | undefined; // what it reads now, if an edit changed it
  font: FontInfo | undefined;
  fit: FitInfo | undefined; // the server's verdict on its edit
  redaction: RedactionInfo | undefined; // the server's verdict on its redaction
  sentences: Sentences;
  focusTo: FocusSpan | null; // focus is sent here: back from its field, or put back from the margin
  notes: Notes; // the page's one note, which this mark shows its own in
  noteId: string; // the page's note, for a screen reader to read with the mark
}

/**
 * A span of the page's text: a click edits it, and so does a tap on one with no note; a note shows on
 * hover, focus and a single tap, and a second tap edits.
 *
 * The page hands it what it shows, so a mark subscribes to nothing and mounts cheaply as the page scrolls
 * near; it redraws only when what it's handed changes. A font's warning waits for an edit (`editNote`).
 */
export const SpanMark = memo(function SpanMark(props: Props) {
  const { span, info, reading, font, fit, redaction, sentences, focusTo, notes, noteId } = props;
  const editor = useEditor();
  const look = lookOf({ span, font, reading, fit, redaction, sentences });
  const described = useSyncExternalStore(notes.store.subscribe, () => notes.store.get().shown?.spanId === span.id);
  const lastTap = useRef(0);
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

  function editAt(event: PressEvent, selects: Press["selects"]) {
    const shownPx = event.target.getBoundingClientRect().width;
    notes.hide(span.id);
    edit(editor, span.id, pressOf(span, { pressX: event.x, shownPx, selects }));
  }

  function pressed(event: PressEvent) {
    // Cmd+Enter is new text on the line below, the window's shortcut: it doesn't edit the span.
    if (isShortcutPress(event)) return;
    // Enter, Space, or a screen reader's activation: edit at once, the whole span selected.
    if (event.pointerType === "keyboard" || event.pointerType === "virtual") {
      notes.hide(span.id);
      edit(editor, span.id, null);
      return;
    }
    // A mouse click edits at once, the caret where it pressed: hovering already showed the note.
    if (event.pointerType === "mouse") {
      editAt(event, "caret");
      return;
    }
    // A touch screen has no hover: with no note to read, a tap edits at once, the word under it selected.
    if (look.note === null) {
      editAt(event, "word");
      return;
    }
    // With one, a tap shows the note, and a second tap edits, the word selected.
    const at = performance.now();
    if (at - lastTap.current < DOUBLE_TAP_MS) {
      lastTap.current = 0;
      editAt(event, "word");
      return;
    }
    lastTap.current = at;
    showNote(event.target, "tap"); // a tap shows the note a pointer would hover for
    // Nothing hovers off on a touch screen, so the next press anywhere closes it.
    window.addEventListener("pointerdown", () => notes.hide(span.id), { once: true, capture: true });
  }

  const className = classes(
    styles.span,
    look.formField && styles.formField,
    look.changed && styles.changed,
    look.trouble && styles.trouble,
    look.redacted && styles.redacted,
    look.unredacted && styles.unredacted,
  );

  return (
    <Button
      ref={takeFocus}
      className={className}
      data-span={span.id}
      data-warned={look.trouble ? "" : undefined}
      style={boxOf(markBoxOf(span, { replaced: reading?.replaced ?? false, fit }), info)}
      aria-describedby={described ? noteId : undefined}
      onPress={pressed}
      // Cmd+Enter goes on to the window's shortcuts, which open new text below: the mark would keep it.
      onPressStart={passShortcut}
      onHoverStart={(event) => showNote(event.target, "hover")}
      onHoverEnd={(event) => {
        // Still focused: its note stays for the keyboard, as the one focus showed.
        if (event.target === document.activeElement) {
          notes.hide(span.id);
          showNote(event.target, "focus");
          return;
        }
        // Not at once: the pointer may be on its way to the note, to read it (WCAG 1.4.13, Hoverable).
        notes.hideSoon(span.id);
      }}
      onFocus={(event) => showNote(event.target, "focus")}
      onBlur={() => {
        notes.hide(span.id);
        if (focusTo !== null) focusMoved(editor);
      }}
      onKeyDown={onEscape(() => notes.hide(span.id))}
    >
      <span className="vh">{reading?.text ?? span.text}</span>
      {look.redacted && <span className="vh"> (redacted)</span>}
    </Button>
  );
});
