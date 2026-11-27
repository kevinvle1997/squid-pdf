import { memo, useCallback, useRef, useSyncExternalStore } from "react";
import { Button, type PressEvent } from "react-aria-components";
import type { PageInfo, Sentences } from "../../api/types";
import { classes } from "../../ui/classes";
import { type Press, pressOf } from "../caret";
import { isShortcutPress, onEscape, passShortcut } from "../commands";
import { DOUBLE_TAP_MS } from "../constants";
import { type FocusSpan, focusMoved } from "../editor";
import type { Line } from "../lines";
import { lineLookOf } from "../marks";
import { redactedRangesOf, type SpanReading } from "../project";
import { redactedBoxes, redactedSentences } from "../redaction";
import type { PageFits, PageRedactions } from "../render";
import { type FontsByObject, fontOf } from "../spanFonts";
import { edit } from "../typing";
import { useEditor } from "./context";
import { boxOf, markBoxOf } from "./geometry";
import type { Notes, Via } from "./notes";
import styles from "./SpanMark.module.css";

interface Props {
  line: Line;
  info: PageInfo;
  readings: ReadonlyMap<string, SpanReading>; // what each span changed reads now
  fonts: FontsByObject;
  fits: PageFits; // the server's verdict on each run's edit
  redactions: PageRedactions; // the server's verdict on each span's redaction
  sentences: Sentences;
  focusTo: FocusSpan | null; // focus is sent to one of its spans: back from its field, or put back from the margin
  notes: Notes; // the page's one note, which this mark shows its own in
  noteId: string; // the page's note, for a screen reader to read with the mark
}

/**
 * A line of several runs, as one mark: a click edits all of it in one field, its caret where it pressed, and so
 * does a tap with no note to read; a note shows on hover, focus and a single tap, and a second tap edits.
 *
 * Handed only what the page holds already, so it's drawn again only when its line's parts change.
 */
export const LineMark = memo(function LineMark(props: Props) {
  const { line, info, readings, fonts, fits, redactions, sentences, focusTo, notes, noteId } = props;
  const editor = useEditor();
  const look = lineLookOf({ spans: line.spans, fonts, readings, fits, redactions, sentences });
  const described = useSyncExternalStore(notes.store.subscribe, () => notes.store.get().shown?.markId === line.id);
  const lastTap = useRef(0);
  // Sent focus, the mark takes it as it's drawn: in the field's place, or where it already stood.
  const takeFocus = useCallback(
    (mark: HTMLButtonElement | null) => {
      if (focusTo !== null) mark?.focus({ preventScroll: focusTo.inPlace });
    },
    [focusTo],
  );

  // While focus is sent here, the note stays shut: it would cover the words just typed or put back.
  const showNote = (anchor: EventTarget, via: Via) => {
    if (look.note !== null && focusTo === null && anchor instanceof Element) notes.show(line.id, anchor, via);
  };

  function editAt(event: PressEvent, selects: Press["selects"]) {
    const shownPx = event.target.getBoundingClientRect().width;
    notes.hide(line.id);
    edit(editor, line.id, pressOf(line, { pressX: event.x, shownPx, selects }));
  }

  function pressed(event: PressEvent) {
    // Cmd+Enter is new text on the line below, the window's shortcut: it doesn't edit the line.
    if (isShortcutPress(event)) return;
    // Enter, Space, or a screen reader's activation: edit at once, the whole line selected.
    if (event.pointerType === "keyboard" || event.pointerType === "virtual") {
      notes.hide(line.id);
      edit(editor, line.id, null);
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
    window.addEventListener("pointerdown", () => notes.hide(line.id), { once: true, capture: true });
  }

  const className = classes(
    styles.span,
    look.changed && styles.changed,
    look.trouble && styles.trouble,
    look.redacted && styles.redacted,
    look.wordsRedacted && styles.wordsRedacted,
    look.unredacted && styles.unredacted,
  );
  // Each run's mark box, a replacement left long reaching past where it ended, made one.
  const boxes = line.spans.map((span) =>
    markBoxOf(span, { replaced: readings.get(span.id)?.replaced ?? false, fit: fits[span.id] }),
  );
  const markBox = { ...line.bbox, x1: Math.max(...boxes.map(({ x1 }) => x1)) };
  const reads = line.spans.map((span) => readings.get(span.id)?.text ?? span.text).join("");
  // Words redacted of each run, said as the server says a redaction.
  const redactedWords = line.spans.flatMap((span) => {
    const ranges = redactedRangesOf(readings.get(span.id));
    return ranges.length === 0 ? [] : [redactedSentences(sentences, { text: span.text, ranges })];
  });

  return (
    <>
      <Button
        ref={takeFocus}
        className={className}
        data-span={line.id}
        data-warned={look.trouble ? "" : undefined}
        style={boxOf(markBox, info)}
        aria-describedby={described ? noteId : undefined}
        onPress={pressed}
        // Cmd+Enter goes on to the window's shortcuts, which open new text below: the mark would keep it.
        onPressStart={passShortcut}
        onHoverStart={(event) => showNote(event.target, "hover")}
        // Not at once: the pointer may be on its way to the note, to read it (WCAG 1.4.13, Hoverable).
        onHoverEnd={() => notes.hideSoon(line.id)}
        onFocus={(event) => showNote(event.target, "focus")}
        onBlur={() => {
          notes.hide(line.id);
          if (focusTo !== null) focusMoved(editor);
        }}
        onKeyDown={onEscape(() => notes.hide(line.id))}
      >
        <span className="vh">{reads}</span>
        {look.redacted && <span className="vh"> (redacted)</span>}
        {redactedWords.length > 0 && <span className="vh"> {redactedWords.join(" ")}</span>}
      </Button>
      {line.spans.flatMap((span) => {
        const ranges = redactedRangesOf(readings.get(span.id));
        const glyphs = fontOf(fonts, span)?.glyphs;
        return redactedBoxes(span, { ranges, glyphs, redaction: redactions[span.id] }).map((box, index) => (
          <span
            // By its span and its place in the line: the ranges don't overlap, and keep their order.
            key={`${span.id}:${ranges[index]?.start}`}
            className={styles.redactedRun}
            aria-hidden="true"
            data-unredacted={look.unredacted ? "" : undefined}
            style={boxOf(box, info)}
          />
        ));
      })}
    </>
  );
});
