import { useRef, useState } from "react";
import { Button, type PressEvent, Tooltip, TooltipTrigger } from "react-aria-components";
import type { Copy, FitInfo, FontInfo, PageInfo, SpanInfo } from "../api/types";
import { DOUBLE_PRESS_MS, NOTE_DELAY_MS } from "./constants";
import { boxOf, markId, useEditor } from "./context";
import styles from "./SpanMark.module.css";
import { fill } from "./words";

interface Note {
  warn: boolean;
  said: string; // the server's sentence
  why: string | null; // why the file's own font can't be used
}

/** What a span's note says, before any edit: its fidelity. Nothing for a span that keeps its font. */
function noteOf(font: FontInfo | undefined, fit: FitInfo | undefined, copy: Copy): Note | null {
  // After an edit, the server's own verdict on it comes first.
  if (fit?.message) return { warn: true, said: fit.message, why: null };
  if (font?.substitute == null) return null;
  const sentence = font.same_widths ? copy.stand_in_same_widths : copy.stand_in;
  return { warn: !font.same_widths, said: fill(sentence, { font: font.substitute }), why: font.why };
}

function WarnMark() {
  return (
    <svg viewBox="0 0 12 12" aria-hidden="true" className={styles.mark}>
      <path d="M6 1.5 11 10.5H1Z" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
      <path d="M6 5v2.4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <circle cx="6" cy="8.9" r=".7" fill="currentColor" />
    </svg>
  );
}

/** A span of the page's text: pressable, with its fidelity on hover, focus and a single tap. */
export function SpanMark({ span, info }: { span: SpanInfo; info: PageInfo }) {
  const editor = useEditor();
  const font = editor.fonts.get(span.font);
  const now = editor.latest.get(span.id);
  const changed = now !== undefined && now !== span.text;
  const fit = changed ? editor.fits[span.id] : undefined;
  const note = noteOf(font, fit, editor.doc.copy);
  const quiet = editor.returnedTo === span.id;
  const [open, setOpen] = useState(false);
  const lastPress = useRef(0);

  function pressed(event: PressEvent) {
    // Enter, Space, or a screen reader's activation: edit at once, the whole span selected.
    if (event.pointerType === "keyboard" || event.pointerType === "virtual") {
      editor.edit(span.id, null);
      return;
    }
    const at = performance.now();
    if (at - lastPress.current < DOUBLE_PRESS_MS) {
      lastPress.current = 0;
      // Where the press landed, in points from the span's start: the word to select.
      const width = (event.target as HTMLElement).getBoundingClientRect().width;
      const atPt = width > 0 ? (event.x / width) * (span.bbox.x1 - span.bbox.x0) : null;
      editor.edit(span.id, atPt);
      return;
    }
    lastPress.current = at;
    setOpen(true); // a tap shows the note a pointer would hover for
    // Nothing hovers off on a touch screen, so the next press anywhere closes it.
    window.addEventListener("pointerdown", () => setOpen(false), { once: true, capture: true });
  }

  const state = [
    styles.span,
    font?.substitute != null && styles.similar,
    changed && styles.changed,
    fit?.message && styles.trouble,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <TooltipTrigger
      delay={NOTE_DELAY_MS}
      closeDelay={0}
      isDisabled={note === null}
      isOpen={open && note !== null && !quiet}
      onOpenChange={setOpen}
    >
      <Button
        id={markId(span.id)}
        className={state}
        style={boxOf(span.bbox, info)}
        onPress={pressed}
        onBlur={() => quiet && editor.returnTo(null)}
      >
        <span className="vh">{now ?? span.text}</span>
      </Button>
      {note !== null && (
        <Tooltip className={styles.note} placement="top" offset={8}>
          <span className={note.warn ? styles.warnHead : styles.head}>
            {note.warn && <WarnMark />}
            {note.said}
          </span>
          {note.why !== null && <span className={styles.why}>{note.why}</span>}
        </Tooltip>
      )}
    </TooltipTrigger>
  );
}
