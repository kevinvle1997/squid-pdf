import { memo, useCallback, useEffect, useEffectEvent, useId, useRef, useState, useSyncExternalStore } from "react";
import { mergeProps, useButton, useFocusRing, useHover, useMove } from "react-aria";
import type { PageInfo, Sentences } from "../../api/types";
import { classes } from "../../ui/classes";
import { coarsePointer, onPointerChange } from "../../ui/pointer";
import { Tooltip } from "../../ui/Tooltip";
import { DOUBLE_TAP_MS, NOTE_DELAY_MS } from "../constants";
import { focusMoved } from "../editor";
import { moveOnPage, moveText, type PagePoint, writeAgain } from "../newText";
import type { InsertReading } from "../project";
import { sentenceOrNothing } from "../words";
import { useEditor } from "./context";
import { boxOf, textBoxOf, textStyleOf } from "./geometry";
import styles from "./TextMark.module.css";

/** A nudge of new text from the keyboard, in points: Shift takes the larger step. */
const NUDGE_PT = 1;
const SHIFT_NUDGE_PT = 10;

interface Props {
  text: InsertReading;
  info: PageInfo;
  face: string; // the face it previews in, for its words while it's dragged
  widthPt: number; // how wide it draws
  takesFocus: boolean; // focus is sent here: back from its field, or moved
  sentences: Sentences;
}

/**
 * New text at rest: a change, marked as one, reached by Tab in page order. Dragged, it moves; focused, the arrows
 * nudge it, a point a press or ten with Shift; Enter or a click types into it again. Its note says so, on hover,
 * focus and a tap; on a touch screen, a second tap types into it.
 */
export const TextMark = memo(function TextMark({ text, info, face, widthPt, takesFocus, sentences }: Props) {
  const editor = useEditor();
  const mark = useRef<HTMLButtonElement | null>(null);
  const noteId = useId();
  const [dragged, setDragged] = useState<PagePoint | null>(null);
  const dragging = useRef<PagePoint | null>(null); // how far it's dragged, as the moves come: drawn as `dragged`
  const moved = useRef(false); // the pointer pressed on the mark has dragged it since
  const cancelled = useRef(false); // the drag was cut short: the text stays where it was
  const pointer = useRef<number | null>(null); // the pointer pressed on the mark, whose drag React Aria tracks
  const [noted, setNoted] = useState(false);
  const waiting = useRef<ReturnType<typeof setTimeout>>(undefined);
  const lastTap = useRef(0);
  // A finger has no keys: its note says how to move it without them.
  const touch = useSyncExternalStore(onPointerChange, coarsePointer);
  const { textId } = text;

  const { buttonProps } = useButton(
    {
      onPress: ({ pointerType }) => {
        // A drag ends with a press: it moved the text, and types into nothing. Keys and a screen reader's press never
        // drag it.
        if (moved.current && pointerType !== "keyboard" && pointerType !== "virtual") return;
        const at = performance.now();
        // A touch screen has no hover: a tap shows the note, and a second tap types into it.
        if (pointerType === "touch" && at - lastTap.current >= DOUBLE_TAP_MS) {
          lastTap.current = at;
          setNoted(true);
          // Nothing hovers off on a touch screen, so the next press anywhere closes it.
          window.addEventListener("pointerdown", () => setNoted(false), { once: true, capture: true });
          return;
        }
        lastTap.current = 0;
        writeAgain(editor, textId);
      },
    },
    mark,
  );
  const { moveProps } = useMove({
    onMoveStart: ({ pointerType }) => {
      if (pointerType === "keyboard") return;
      // Dragged, it says nothing: the note would hang where it was.
      moved.current = true;
      clearTimeout(waiting.current);
      setNoted(false);
    },
    onMove: ({ pointerType, deltaX, deltaY, shiftKey }) => {
      if (pointerType === "keyboard") {
        const step = shiftKey ? SHIFT_NUDGE_PT : NUDGE_PT;
        moveText(editor, textId, { by: [deltaX * step, deltaY * step], nudge: true, widthPt });
        return;
      }
      const shownWidthPx =
        mark.current?.offsetParent instanceof HTMLElement ? mark.current.offsetParent.offsetWidth : 0;
      const [dx, dy] = moveOnPage([deltaX, deltaY], { shownWidthPx, info });
      const [x, y] = dragging.current ?? [0, 0];
      dragging.current = [x + dx, y + dy];
      setDragged(dragging.current);
    },
    onMoveEnd: ({ pointerType }) => {
      const by = dragging.current;
      dragging.current = null;
      setDragged(null);
      // Cancelled, it's put back.
      if (cancelled.current) {
        cancelled.current = false;
        return;
      }
      if (pointerType !== "keyboard" && by !== null) moveText(editor, textId, { by, nudge: false, widthPt });
    },
  });
  /** Cut the drag short as the browser cancels one, so every hook on the mark ends it and the next press starts afresh. */
  function cutShort() {
    if (dragging.current === null) return;
    const cancel = new PointerEvent("pointercancel", { pointerId: pointer.current ?? undefined, bubbles: true });
    mark.current?.dispatchEvent(cancel);
  }
  const onWindowBlur = useEffectEvent(cutShort);
  // Heard on the window, as `useMove` hears it, since the browser aims it at what's under the pointer, which may be
  // a line drawn over the mark; and first, before the drag's end, which React Aria says the same way for a drop.
  const onPointerCancel = useEffectEvent(({ pointerId }: PointerEvent) => {
    if (pointerId === pointer.current) cancelled.current = dragging.current !== null;
  });
  useEffect(() => {
    window.addEventListener("blur", onWindowBlur);
    window.addEventListener("pointercancel", onPointerCancel, { capture: true });
    return () => {
      window.removeEventListener("blur", onWindowBlur);
      window.removeEventListener("pointercancel", onPointerCancel, { capture: true });
    };
  }, []);
  const { hoverProps, isHovered } = useHover({
    onHoverStart: () => {
      clearTimeout(waiting.current);
      waiting.current = setTimeout(() => setNoted(true), NOTE_DELAY_MS);
    },
    onHoverEnd: () => {
      clearTimeout(waiting.current);
      setNoted(false);
    },
  });
  const { focusProps, isFocusVisible } = useFocusRing();
  // Sent focus, the mark takes it as it's drawn: back from its field, or where it stood before a move.
  const takeFocus = useCallback(
    (element: HTMLButtonElement | null) => {
      mark.current = element;
      if (takesFocus) element?.focus();
    },
    [takesFocus],
  );

  const [dx, dy] = dragged ?? [0, 0];
  const where = { ...text.edit, origin: [text.edit.origin[0] + dx, text.edit.origin[1] + dy] as [number, number] };
  // A reading persisted before the note's sentences were sent has none to show.
  const heading = sentenceOrNothing(sentences.new_text);
  const detail = sentenceOrNothing(touch ? sentences.new_text_moves_touch : sentences.new_text_moves);
  const showsNote = (noted || isFocusVisible) && dragged === null && (heading !== "" || detail !== "");
  return (
    <>
      <button
        {...mergeProps(buttonProps, moveProps, hoverProps, focusProps, {
          onBlur: () => {
            if (takesFocus) focusMoved(editor);
          },
          // Each press starts afresh: a drop let go where the mark doesn't hear it (over a line drawn on it, or outside
          // the window) ends with no press. A second finger on the mark mid-drag is no press of its own: the first
          // one drags.
          onPointerDown: ({ pointerId, isPrimary }: { pointerId: number; isPrimary: boolean }) => {
            if (!isPrimary) return;
            pointer.current = pointerId;
            moved.current = false;
          },
          onLostPointerCapture: cutShort,
        })}
        ref={takeFocus}
        type="button"
        className={classes(styles.text, isHovered && styles.hovered, isFocusVisible && styles.focused)}
        style={boxOf(textBoxOf(where, widthPt), info)}
        aria-describedby={showsNote ? noteId : undefined}
        data-text={textId}
      >
        {/* Dragged, its words go with the pointer, over the ink left where it was until it's put down. */}
        {dragged === null ? (
          <span className="vh">{text.edit.text}</span>
        ) : (
          <span className={styles.words} style={wordsStyleOf(where, info, face)}>
            {text.edit.text}
          </span>
        )}
      </button>
      <Tooltip
        id={noteId}
        triggerRef={mark}
        isOpen={showsNote}
        onOpenChange={(isOpen) => isOpen || setNoted(false)}
        heading={heading}
      >
        {detail}
      </Tooltip>
    </>
  );
});

/** New text's words as they're set, wherever the box they're drawn in is. */
function wordsStyleOf(insert: InsertReading["edit"], info: PageInfo, face: string) {
  const { left: _left, top: _top, height: _height, ...words } = textStyleOf(insert, info, face);
  return words;
}
