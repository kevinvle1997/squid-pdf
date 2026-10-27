import { memo, useCallback, useId, useRef, useState } from "react";
import { mergeProps, useButton, useFocusRing, useHover, useMove } from "react-aria";
import type { PageInfo } from "../../api/types";
import { classes } from "../../ui/classes";
import { Tooltip } from "../../ui/Tooltip";
import { NOTE_DELAY_MS } from "../constants";
import { focusMoved } from "../editor";
import { moveOnPage, moveText, type PagePoint, writeAgain } from "../newText";
import type { InsertReading } from "../project";
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
}

/**
 * New text at rest: a change, marked as one, reached by Tab in page order. Dragged, it moves; focused, the arrows
 * nudge it, a point a press or ten with Shift; Enter or a click types into it again. Its note says so.
 */
export const TextMark = memo(function TextMark({ text, info, face, widthPt, takesFocus }: Props) {
  const editor = useEditor();
  const mark = useRef<HTMLButtonElement | null>(null);
  const noteId = useId();
  const [dragged, setDragged] = useState<PagePoint | null>(null);
  const dragging = useRef<PagePoint | null>(null); // how far it's dragged, as the moves come: drawn as `dragged`
  const moved = useRef(false);
  const [noted, setNoted] = useState(false);
  const waiting = useRef<ReturnType<typeof setTimeout>>(undefined);
  const { textId } = text;

  const { buttonProps } = useButton(
    {
      onPress: () => {
        // A drag ends with a press: it moved the text, and types into nothing.
        if (moved.current) {
          moved.current = false;
          return;
        }
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
        moveText(editor, textId, { by: [deltaX * step, deltaY * step], nudge: true });
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
      if (pointerType !== "keyboard" && by !== null) moveText(editor, textId, { by, nudge: false });
    },
  });
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
  const showsNote = (noted || isFocusVisible) && dragged === null;
  return (
    <>
      <button
        {...mergeProps(buttonProps, moveProps, hoverProps, focusProps, {
          onBlur: () => {
            if (takesFocus) focusMoved(editor);
          },
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
        heading="Text you added"
      >
        Drag it to move it, or nudge it with the arrow keys, Shift for a bigger step. Enter types in it again.
      </Tooltip>
    </>
  );
});

/** New text's words as they're set, wherever the box they're drawn in is. */
function wordsStyleOf(insert: InsertReading["edit"], info: PageInfo, face: string) {
  const { left: _left, top: _top, height: _height, ...words } = textStyleOf(insert, info, face);
  return words;
}
