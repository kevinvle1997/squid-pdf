import {
  type FocusEvent,
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
import { type Editor, type FocusNewText, focusMoved } from "../editor";
import { moveNewText, moveOnPage, newTextFaceOf, openNewTextAgain, type PagePoint } from "../newText";
import type { PageState } from "../page";
import type { NewTextReading } from "../project";
import { sentenceOrNothing } from "../words";
import { useEditor } from "./context";
import { useFaceOnPage } from "./faceOnPage";
import { boxOf, newTextBoxOf, newTextWidthIn, newTextWordsStyleOf } from "./geometry";
import styles from "./NewTextMark.module.css";
import { isShowing, type Notes } from "./notes";

const NUDGE_PT = 1;
const SHIFT_NUDGE_PT = 10;

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
      focusTo={page.focusNewText?.newTextId === text.newTextId ? page.focusNewText : null}
      sentences={page.sentences}
      notes={notes}
    />
  );
}

interface Props {
  text: NewTextReading;
  info: PageInfo;
  face: string;
  widthPt: number;
  focusTo: FocusNewText | null;
  sentences: Sentences;
  notes: Notes;
}

const NewTextMark = memo(function NewTextMark({ text, info, face, widthPt, focusTo, sentences, notes }: Props) {
  const editor = useEditor();
  const { newTextId } = text;
  const mark = useRef<HTMLButtonElement | null>(null);
  const noteId = useId();
  // Its name among the page's notes, apart from every span's, whose ids are hex.
  const note = useNote(notes, `new text ${newTextId}`);
  const drag = useDrag(editor, { newTextId, info, widthPt, mark, onStart: note.hide });
  const takeFocus = useTakeFocus(mark, focusTo, { focused: note.showFocus, gone: note.hide });
  const touch = useSyncExternalStore(onPointerChange, coarsePointer);
  const { buttonProps } = useButton(
    {
      onPress: ({ pointerType, target }) => {
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

  const [dx, dy] = drag.by ?? [0, 0];
  const where = { ...text.edit, origin: [text.edit.origin[0] + dx, text.edit.origin[1] + dy] as [number, number] };
  const heading = sentenceOrNothing(sentences.new_text);
  const detail = sentenceOrNothing(touch ? sentences.new_text_moves_touch : sentences.new_text_moves);
  const showsNote = isShowing(note.shown, isFocusVisible) && drag.by === null && (heading !== "" || detail !== "");
  return (
    <>
      <button
        {...mergeProps(buttonProps, drag.props, note.props, focusProps, {
          onBlur: () => {
            if (focusTo !== null) focusMoved(editor);
          },
        })}
        ref={takeFocus}
        type="button"
        className={classes(styles.text, note.hovered && styles.hovered, isFocusVisible && styles.focused)}
        style={boxOf(newTextBoxOf(where, widthPt), info)}
        aria-describedby={showsNote ? noteId : undefined}
        data-new-text={newTextId}
      >
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
        // Escape, or a press on it, closes it until focus comes again (WCAG 1.4.13, Dismissible).
        onOpenChange={(isOpen) => isOpen || note.hide()}
        onHoverChange={note.hoveredNote}
        heading={heading}
      >
        {detail}
      </Tooltip>
    </>
  );
});

function useNote(notes: Notes, markId: string) {
  const shown = useSyncExternalStore(notes.store.subscribe, () => {
    const { shown } = notes.store.get();
    return shown?.markId === markId ? shown : null;
  });
  const lastTap = useRef(0);
  const hide = useCallback(() => notes.hide(markId), [notes, markId]);
  const { hoverProps, isHovered } = useHover({
    onHoverStart: ({ target }) => notes.show(markId, target, "hover"),
    onHoverEnd: () => notes.hideSoon(markId),
  });
  const showFocus = useCallback((mark: Element) => notes.show(markId, mark, "focus"), [notes, markId]);
  const props = mergeProps(hoverProps, {
    onFocus: ({ currentTarget }: FocusEvent<Element>) => showFocus(currentTarget),
    onBlur: hide,
  });
  function hoveredNote(isHovered: boolean) {
    if (isHovered) notes.keep(markId);
    else notes.hideSoon(markId);
  }
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
  return { shown, hovered: isHovered, props, hide, showFocus, hoveredNote, tappedTwice };
}

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
  const dragging = useRef<PagePoint | null>(null);
  const moved = useRef(false);
  const cancelled = useRef(false);
  const pointer = useRef<number | null>(null);
  const { moveProps } = useMove({
    onMoveStart: ({ pointerType }) => {
      if (pointerType === "keyboard") return;
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
      if (cancelled.current) {
        cancelled.current = false;
        return;
      }
      if (pointerType !== "keyboard" && dropped !== null) {
        moveNewText(editor, newTextId, { by: dropped, nudge: false, widthPt });
      }
    },
  });
  function cutShort() {
    if (dragging.current === null) return;
    const cancel = new PointerEvent("pointercancel", { pointerId: pointer.current ?? undefined, bubbles: true });
    mark.current?.dispatchEvent(cancel);
  }
  const onWindowBlur = useEffectEvent(cutShort);
  // Heard on the window: the browser aims it at what's under the pointer, which may be a line drawn over the mark.
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
    // Each press starts afresh: a drop let go where the mark can't hear it ends with no press.
    onPointerDown: ({ pointerId, isPrimary }: { pointerId: number; isPrimary: boolean }) => {
      if (!isPrimary) return;
      pointer.current = pointerId;
      moved.current = false;
    },
    onLostPointerCapture: cutShort,
  });
  function endedBy(pointerType: PressEvent["pointerType"]): boolean {
    return moved.current && pointerType !== "keyboard" && pointerType !== "virtual";
  }
  return { by, props, endedBy };
}

function useTakeFocus(
  mark: RefObject<HTMLButtonElement | null>,
  focusTo: FocusNewText | null,
  { focused, gone }: { focused: (mark: Element) => void; gone: () => void },
) {
  return useCallback(
    (element: HTMLButtonElement | null) => {
      mark.current = element;
      const focusStays = element !== null && element === document.activeElement;
      if (focusStays) focused(element);
      else if (focusTo !== null) element?.focus({ preventScroll: focusTo.inPlace });
      return () => {
        mark.current = null;
        gone();
      };
    },
    [mark, focusTo, focused, gone],
  );
}
