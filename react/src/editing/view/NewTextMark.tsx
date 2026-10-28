import {
  memo,
  type RefObject,
  useCallback,
  useEffect,
  useEffectEvent,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { mergeProps, type PressEvent, useButton, useFocusRing, useHover, useMove } from "react-aria";
import type { PageInfo, Sentences } from "../../api/types";
import { classes } from "../../ui/classes";
import { coarsePointer, onPointerChange } from "../../ui/pointer";
import { Tooltip } from "../../ui/Tooltip";
import { DOUBLE_TAP_MS } from "../constants";
import { type Editor, focusMoved } from "../editor";
import { moveNewText, moveOnPage, newTextFaceOf, openNewTextAgain, type PagePoint } from "../newText";
import type { PageState } from "../page";
import type { NewTextReading } from "../project";
import { sentenceOrNothing } from "../words";
import { useEditor } from "./context";
import { useFaceOnPage } from "./faceOnPage";
import { boxOf, newTextBoxOf, newTextWidthIn, newTextWordsStyleOf } from "./geometry";
import styles from "./NewTextMark.module.css";
import type { Notes } from "./notes";

/** A nudge of new text from the keyboard, in points: Shift takes the larger step. */
const NUDGE_PT = 1;
const SHIFT_NUDGE_PT = 10;

/** New text at rest on `page`, measured in the face that previews it, again as that face loads. */
export function RestingNewText({
  text,
  page,
  info,
  notes,
}: {
  text: NewTextReading;
  page: PageState;
  info: PageInfo;
  notes: Notes;
}) {
  const face = newTextFaceOf(page.layout, text.edit);
  useFaceOnPage(face);
  return (
    <NewTextMark
      text={text}
      info={info}
      face={face}
      widthPt={newTextWidthIn(page.layout, text.edit)}
      takesFocus={page.focusNewText === text.newTextId}
      sentences={page.sentences}
      notes={notes}
    />
  );
}

interface Props {
  text: NewTextReading;
  info: PageInfo;
  face: string; // the face it previews in, for its words while it's dragged
  widthPt: number; // how wide it draws
  takesFocus: boolean; // focus is sent here: back from its field, or moved
  sentences: Sentences;
  notes: Notes; // the page's, which shows one mark's note at a time
}

/**
 * New text at rest: a change, marked as one, reached by Tab in page order. Dragged, it moves; focused, the arrows
 * nudge it, a point a press or ten with Shift; Enter or a click types into it again. Its note says so, on hover,
 * focus and a tap; on a touch screen, a second tap types into it.
 */
const NewTextMark = memo(function NewTextMark({ text, info, face, widthPt, takesFocus, sentences, notes }: Props) {
  const editor = useEditor();
  const { newTextId } = text;
  const mark = useRef<HTMLButtonElement | null>(null);
  const noteId = useId();
  // Its name among the page's notes, apart from every span's, whose ids are hex.
  const note = useNote(notes, `new text ${newTextId}`);
  const drag = useDrag(editor, { newTextId, info, widthPt, mark, onStart: note.hide });
  const takeFocus = useTakeFocus(mark, takesFocus, note.hide);
  // A finger has no keys: its note says how to move it without them.
  const touch = useSyncExternalStore(onPointerChange, coarsePointer);
  const { buttonProps } = useButton(
    {
      onPress: ({ pointerType, target }) => {
        // A drag ends with a press: it moved the text, and types into nothing.
        if (drag.endedBy(pointerType)) return;
        // A touch screen has no hover: a tap shows the note, and a second tap types into it.
        if (pointerType === "touch" && !note.tappedTwice(target)) return;
        note.hide();
        openNewTextAgain(editor, newTextId);
      },
    },
    mark,
  );
  const { focusProps, isFocusVisible } = useFocusRing();
  // Escape, or a press on it, closes the note focus showed until focus leaves (WCAG 1.4.13, Dismissible).
  const [dismissed, setDismissed] = useState(false);

  const [dx, dy] = drag.by ?? [0, 0];
  const where = { ...text.edit, origin: [text.edit.origin[0] + dx, text.edit.origin[1] + dy] as [number, number] };
  // A reading persisted before the note's sentences were sent has none to show.
  const heading = sentenceOrNothing(sentences.new_text);
  const detail = sentenceOrNothing(touch ? sentences.new_text_moves_touch : sentences.new_text_moves);
  const showsNote =
    (note.shown || (isFocusVisible && !dismissed)) && drag.by === null && (heading !== "" || detail !== "");
  return (
    <>
      <button
        {...mergeProps(buttonProps, drag.props, note.hoverProps, focusProps, {
          onBlur: () => {
            setDismissed(false);
            if (takesFocus) focusMoved(editor);
          },
        })}
        ref={takeFocus}
        type="button"
        className={classes(styles.text, note.hovered && styles.hovered, isFocusVisible && styles.focused)}
        style={boxOf(newTextBoxOf(where, widthPt), info)}
        aria-describedby={showsNote ? noteId : undefined}
        data-new-text={newTextId}
      >
        {/* Dragged, its words go with the pointer, over the ink left where it was until it's put down. */}
        {drag.by === null ? (
          <span className="vh">{text.edit.text}</span>
        ) : (
          <span className={styles.words} style={newTextWordsStyleOf(where, info, face)}>
            {text.edit.text}
          </span>
        )}
      </button>
      <Tooltip
        id={noteId}
        triggerRef={mark}
        isOpen={showsNote}
        onOpenChange={(isOpen) => {
          if (isOpen) return;
          note.hide();
          if (isFocusVisible) setDismissed(true);
        }}
        // The pointer leaving it is no dismissal: a note focus showed stays (WCAG 1.4.13, Persistent).
        onHoverChange={note.hoveredNote}
        heading={heading}
      >
        {detail}
      </Tooltip>
    </>
  );
});

/** The mark's note, shown and closed by the page's notes as a span's is (`view/notes.ts`), known there by `markId`. */
function useNote(notes: Notes, markId: string) {
  const shown = useSyncExternalStore(notes.store.subscribe, () => notes.store.get().shown?.markId === markId);
  const lastTap = useRef(0);
  const hide = useCallback(() => notes.hide(markId), [notes, markId]);
  const { hoverProps, isHovered } = useHover({
    onHoverStart: ({ target }) => notes.show(markId, target, "hover"),
    onHoverEnd: () => notes.hideSoon(markId),
  });
  /** The pointer came onto the note, or left it. */
  function hoveredNote(isHovered: boolean) {
    if (isHovered) notes.keep(markId);
    else notes.hideSoon(markId);
  }
  /** A tap on `mark`: the first shows the note, and says no; one soon after it says yes, to type in it. */
  function tappedTwice(mark: Element): boolean {
    const at = performance.now();
    if (at - lastTap.current < DOUBLE_TAP_MS) {
      lastTap.current = 0;
      return true;
    }
    lastTap.current = at;
    notes.show(markId, mark, "tap");
    // Nothing hovers off on a touch screen, so the next press anywhere closes it.
    window.addEventListener("pointerdown", hide, { once: true, capture: true });
    return false;
  }
  return { shown, hovered: isHovered, hoverProps, hide, hoveredNote, tappedTwice };
}

/**
 * The mark's drag: drawn moved `by` points as the pointer goes, then a step of the history as it's put down; the
 * keyboard's arrows nudge it at once. A drag cut short (`pointercancel`, `lostpointercapture`, the window's blur)
 * puts it back.
 */
function useDrag(
  editor: Editor,
  {
    newTextId,
    info,
    widthPt,
    mark,
    onStart,
  }: {
    newTextId: number;
    info: PageInfo;
    widthPt: number;
    mark: RefObject<HTMLButtonElement | null>;
    onStart: () => void;
  },
) {
  const [by, setBy] = useState<PagePoint | null>(null);
  const dragging = useRef<PagePoint | null>(null); // how far it's dragged, as the moves come: drawn as `by`
  const moved = useRef(false); // the pointer pressed on the mark has dragged it since
  const cancelled = useRef(false); // the drag was cut short: the text stays where it was
  const pointer = useRef<number | null>(null); // the pointer pressed on the mark, whose drag React Aria tracks
  const { moveProps } = useMove({
    onMoveStart: ({ pointerType }) => {
      if (pointerType === "keyboard") return;
      // Dragged, it says nothing: the note would hang where it was.
      moved.current = true;
      onStart();
    },
    onMove: ({ pointerType, deltaX, deltaY, shiftKey }) => {
      if (pointerType === "keyboard") {
        const step = shiftKey ? SHIFT_NUDGE_PT : NUDGE_PT;
        moveNewText(editor, newTextId, { by: [deltaX * step, deltaY * step], nudge: true, widthPt });
        return;
      }
      const layer = mark.current?.offsetParent;
      const shownWidthPx = layer instanceof HTMLElement ? layer.offsetWidth : 0;
      const [dx, dy] = moveOnPage([deltaX, deltaY], { shownWidthPx, info });
      const [x, y] = dragging.current ?? [0, 0];
      dragging.current = [x + dx, y + dy];
      setBy(dragging.current);
    },
    onMoveEnd: ({ pointerType }) => {
      const dropped = dragging.current;
      dragging.current = null;
      setBy(null);
      // Cancelled, it's put back.
      if (cancelled.current) {
        cancelled.current = false;
        return;
      }
      if (pointerType !== "keyboard" && dropped !== null) {
        moveNewText(editor, newTextId, { by: dropped, nudge: false, widthPt });
      }
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
  const props = mergeProps(moveProps, {
    // Each press starts afresh: a drop let go where the mark doesn't hear it (over a line drawn on it, or outside the
    // window) ends with no press. A second finger on the mark mid-drag is no press of its own: the first one drags.
    onPointerDown: ({ pointerId, isPrimary }: { pointerId: number; isPrimary: boolean }) => {
      if (!isPrimary) return;
      pointer.current = pointerId;
      moved.current = false;
    },
    onLostPointerCapture: cutShort,
  });
  /** Whether a press is the end of a drag just put down: it moved the text, and is no press of its own. */
  function endedBy(pointerType: PressEvent["pointerType"]): boolean {
    // Keys and a screen reader's press never drag it.
    return moved.current && pointerType !== "keyboard" && pointerType !== "virtual";
  }
  return { by, props, endedBy };
}

/**
 * A ref for the mark: sent focus, it takes it as it's drawn, back from its field or where it stood before a move.
 * Gone, it lets go of its note (`letGo`): the page's notes hear no blur or hover's end from a mark taken away.
 */
function useTakeFocus(mark: RefObject<HTMLButtonElement | null>, takesFocus: boolean, letGo: () => void) {
  return useCallback(
    (element: HTMLButtonElement | null) => {
      mark.current = element;
      if (takesFocus) element?.focus();
      return () => {
        mark.current = null;
        letGo();
      };
    },
    [mark, takesFocus, letGo],
  );
}
