import { memo, useCallback, useRef, useState } from "react";
import { Button, type PressEvent, TooltipTrigger } from "react-aria-components";
import type { Copy, FitInfo, FontInfo, PageInfo, SpanInfo } from "../../api/types";
import { Tooltip } from "../../ui/Tooltip";
import { DOUBLE_PRESS_MS, NOTE_DELAY_MS } from "../constants";
import { type FocusTo, focusMoved } from "../editor";
import { lookOf } from "../marks";
import type { SpanReading } from "../project";
import { edit } from "../typing";
import { useEditor } from "./context";
import { boxOf } from "./geometry";
import styles from "./SpanMark.module.css";

interface Props {
  span: SpanInfo;
  info: PageInfo;
  edited: SpanReading | undefined; // what it reads now, if an edit changed it
  font: FontInfo | undefined;
  fit: FitInfo | undefined; // the server's verdict on its edit
  copy: Copy;
  focusTo: FocusTo | null; // focus is sent here: back from its field, or put back from the margin
}

/**
 * A span of the page's text: pressable, with its fidelity on hover, focus and a single tap.
 * The page hands it what it shows, so a page's marks subscribe to nothing of their own and
 * mount cheaply as the page scrolls near; each redraws only when what it's handed changes.
 */
export const SpanMark = memo(function SpanMark({ span, info, edited, font, fit, copy, focusTo }: Props) {
  const editor = useEditor();
  const look = lookOf({ font, edited, fit, copy });
  const note = look.note;
  const [open, setOpen] = useState(false);
  const lastPress = useRef(0);
  // Sent focus, the mark takes it as it's drawn: in the field's place, or where it already stood.
  const takeFocus = useCallback(
    (mark: HTMLButtonElement | null) => {
      if (focusTo !== null) mark?.focus();
    },
    [focusTo],
  );

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

  const className = [
    styles.span,
    look.similar && styles.similar,
    look.changed && styles.changed,
    look.trouble && styles.trouble,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <TooltipTrigger
      delay={NOTE_DELAY_MS}
      closeDelay={0}
      isDisabled={note === null}
      isOpen={open && note !== null && !focusTo?.noteShut}
      onOpenChange={setOpen}
    >
      <Button
        ref={takeFocus}
        className={className}
        style={boxOf(span.bbox, info)}
        onPress={pressed}
        onBlur={() => focusTo !== null && focusMoved(editor)}
      >
        <span className="vh">{edited?.text ?? span.text}</span>
      </Button>
      {note !== null && (
        <Tooltip heading={note.said} warn={note.warn}>
          {note.why}
        </Tooltip>
      )}
    </TooltipTrigger>
  );
});
