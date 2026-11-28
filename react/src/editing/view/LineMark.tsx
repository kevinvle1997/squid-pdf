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
import { baselineOf, boxOf, markBoxOf } from "./geometry";
import type { Notes, Via } from "./notes";
import styles from "./SpanMark.module.css";

interface Props {
  line: Line;
  info: PageInfo;
  readings: ReadonlyMap<string, SpanReading>;
  fonts: FontsByObject;
  fits: PageFits;
  redactions: PageRedactions;
  sentences: Sentences;
  focusTo: FocusSpan | null;
  notes: Notes;
  noteId: string;
}

export const LineMark = memo(function LineMark(props: Props) {
  const { line, info, readings, fonts, fits, redactions, sentences, focusTo, notes, noteId } = props;
  const editor = useEditor();
  const look = lineLookOf({ spans: line.spans, fonts, readings, fits, redactions, sentences });
  const described = useSyncExternalStore(notes.store.subscribe, () => notes.store.get().shown?.markId === line.id);
  const lastTap = useRef(0);
  const takeFocus = useCallback(
    (mark: HTMLButtonElement | null) => {
      if (focusTo !== null) mark?.focus({ preventScroll: focusTo.inPlace });
    },
    [focusTo],
  );

  const showNote = (anchor: EventTarget, via: Via) => {
    if (look.note !== null && focusTo === null && anchor instanceof Element) notes.show(line.id, anchor, via);
  };

  function editAt(event: PressEvent, selects: Press["selects"]) {
    const shownPx = event.target.getBoundingClientRect().width;
    notes.hide(line.id);
    edit(editor, line.id, pressOf(line, { pressX: event.x, shownPx, selects }));
  }

  function pressed(event: PressEvent) {
    if (isShortcutPress(event)) return;
    if (event.pointerType === "keyboard" || event.pointerType === "virtual") {
      notes.hide(line.id);
      edit(editor, line.id, null);
      return;
    }
    if (event.pointerType === "mouse") {
      editAt(event, "caret");
      return;
    }
    if (look.note === null) {
      editAt(event, "word");
      return;
    }
    const at = performance.now();
    if (at - lastTap.current < DOUBLE_TAP_MS) {
      lastTap.current = 0;
      editAt(event, "word");
      return;
    }
    lastTap.current = at;
    showNote(event.target, "tap");
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
  const fit = line.spans.map((span) => fits[span.id]).find((each) => each !== undefined);
  const markBox = markBoxOf(line, { replaced: look.changed, fit });
  const reads = line.spans.map((span) => readings.get(span.id)?.text ?? span.text).join("");
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
        style={{ ...boxOf(markBox, info), ...baselineOf(markBox, line.spans[0]?.origin[1] ?? markBox.y1) }}
        aria-describedby={described ? noteId : undefined}
        onPress={pressed}
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
