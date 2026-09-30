import { memo, useRef, useState } from "react";
import { Button, type PressEvent, TooltipTrigger } from "react-aria-components";
import type { Copy, FitInfo, FontInfo, PageInfo, SpanInfo } from "../../api/types";
import { Tooltip } from "../../ui/Tooltip";
import { DOUBLE_PRESS_MS, NOTE_DELAY_MS } from "../constants";
import type { SpanView } from "../project";
import { edit, returnTo } from "../typing";
import { fill } from "../words";
import { boxOf, markId, useEditor } from "./context";
import styles from "./SpanMark.module.css";

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

interface Props {
  span: SpanInfo;
  info: PageInfo;
  now: SpanView | undefined; // what it reads now, if an edit changed it
  font: FontInfo | undefined;
  fit: FitInfo | undefined; // the server's verdict on its edit
  copy: Copy;
  quiet: boolean; // focus just came back from editing it: its note would cover the new words
}

/**
 * A span of the page's text: pressable, with its fidelity on hover, focus and a single tap.
 * The page hands it what it shows, so a page's marks subscribe to nothing of their own and
 * mount cheaply as the page scrolls near; each redraws only when what it's handed changes.
 */
export const SpanMark = memo(function SpanMark({ span, info, now, font, fit, copy, quiet }: Props) {
  const editor = useEditor();
  const changed = now?.replaced ?? false;
  const note = noteOf(font, changed ? fit : undefined, copy);
  const [open, setOpen] = useState(false);
  const lastPress = useRef(0);

  function pressed(event: PressEvent) {
    // Enter, Space, or a screen reader's activation: edit at once, the whole span selected.
    if (event.pointerType === "keyboard" || event.pointerType === "virtual") {
      edit(editor, span.id, null);
      return;
    }
    const at = performance.now();
    if (at - lastPress.current < DOUBLE_PRESS_MS) {
      lastPress.current = 0;
      // Where the press landed, in points from the span's start: the word to select.
      const width = (event.target as HTMLElement).getBoundingClientRect().width;
      const atPt = width > 0 ? (event.x / width) * (span.bbox.x1 - span.bbox.x0) : null;
      edit(editor, span.id, atPt);
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
        // Back from editing this span: the mark takes the field's place and focus with it.
        autoFocus={quiet}
        onBlur={() => quiet && returnTo(editor, null)}
      >
        <span className="vh">{now?.text ?? span.text}</span>
      </Button>
      {note !== null && (
        <Tooltip heading={note.said} warn={note.warn}>
          {note.why}
        </Tooltip>
      )}
    </TooltipTrigger>
  );
});
