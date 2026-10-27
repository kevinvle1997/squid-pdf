import {
  type DragEvent,
  type DragEventHandler,
  type FocusEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useReducer,
  useRef,
  useState,
} from "react";
import { isFileDropItem, useDrop } from "react-aria";
import { Input, TextField } from "react-aria-components";
import type { PageInfo, SpanInfo } from "../../api/types";
import { selectionAt } from "../caret";
import { carriesFiles } from "../dropped";
import { focusMoved } from "../editor";
import { addFaces, DEFAULT_FACE, previewFaceOf } from "../faces";
import { fitOf, runsPast, settingOf } from "../fit";
import { attachDropped } from "../fonts";
import { editableValueIn } from "../formattedValues";
import { learnEveryLanguage } from "../monthNames";
import { textIn } from "../project";
import { fontOf } from "../spanFonts";
import { shallowEqual } from "../store";
import { enter, fieldFocused, finish, stepWayOut, troublesIn, typeInto } from "../typing";
import { openValueEditor } from "../valueEditor";
import { Chip } from "./Chip";
import { useEditor, useEditorState } from "./context";
import styles from "./EditField.module.css";
import { boxOf, fieldPartOf, fieldScaleOf, fieldWidthPt, points, spanTextStyle } from "./geometry";

/**
 * Typing in place: the span's text in the face that will draw it, checked as it's typed.
 * It stands where the span's mark stood, so Tab and Shift+Tab go on to the next span unaided.
 * The editor holds what's typed, so an export mid-word takes it too.
 */
export function EditField({ span, info }: { span: SpanInfo; info: PageInfo }) {
  const editor = useEditor();
  const text = useEditorState((state) => state.draft?.text ?? "");
  const font = useEditorState((state) => fontOf(state.layout.fonts, span));
  const start = useEditorState((state) => textIn(state.reading, span));
  const face = font === undefined ? DEFAULT_FACE : previewFaceOf(span, { font, text });
  // A letter the look-alike lacks sends the line to its broadest face, put on the page only once one does.
  useEffect(() => addFaces([face]), [face]);
  const placed = useRef(false);
  const glyphs = font?.glyphs ?? {};
  // A line past its room offers the ways out, and the one chosen is drawn here at once, as the server will.
  const rules = useEditorState((state) => state.doc.fit);
  const asked = useEditorState((state) => state.draft?.strategy);
  const setting = settingOf(span, { font, text, asked, rules });
  // Left long, a line past its room shows where the room ends: what to shorten it to.
  const fit = font === undefined ? undefined : fitOf(span, { font, text, rules });
  const pastRoom = fit !== undefined && setting.way === "as-is" && runsPast(fit.deltaPt, fit.roomPt, rules);
  // What's typed comes to, worked out once a change: its troubles, a line each, the server's then the fit's own,
  // and its verdict.
  const { trouble, fidelity, why, substitute, sameWidths } = useEditorState((state) => {
    const { sentences, verdict } = troublesIn(state, state.draft?.text ?? "");
    const comesTo = verdict ?? span;
    const { fidelity, why, substitute, same_widths: sameWidths } = comesTo;
    return { trouble: sentences.join("\n"), fidelity, why, substitute, sameWidths };
  }, shallowEqual);
  const verdict = { fidelity, why, substitute, same_widths: sameWidths };
  // A date or an amount in what's typed, the one the press that opened the field landed on, else the first.
  const press = useEditorState((state) => state.draft?.press ?? null);
  const dateOrder = useEditorState((state) => state.layout.dateOrder);
  const at = press === null ? undefined : selectionAt(start, press, { glyphs, span })[0];
  const value = editableValueIn(text, { dateOrder, at });
  // Its month's name may be in a language Intl is asked of only as it's needed.
  useEffect(learnEveryLanguage, []);
  // Sent back from the value's editor in the slip, the field takes focus again.
  const refocus = useEditorState((state) => {
    const { focusTo } = state;
    return focusTo !== null && "spanId" in focusTo && focusTo.spanId === span.id ? focusTo : null;
  });
  const field = useRef<HTMLInputElement>(null);
  const slip = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [, measureAgain] = useReducer((times: number) => times + 1, 0);
  // As the page is shown wider or narrower, what the field scales by to show the span's size.
  const watchPage = useCallback(
    (input: HTMLInputElement | null) => {
      field.current = input;
      if (refocus !== null) input?.focus();
      const layer = input?.offsetParent;
      if (input == null || !(layer instanceof HTMLElement)) return;
      const resized = new ResizeObserver(() => {
        const fontPx = parseFloat(getComputedStyle(input).fontSize);
        setScale(fieldScaleOf(span.size, { pageWidthPt: info.width, shownPx: layer.clientWidth, fontPx }));
      });
      resized.observe(layer);
      // A face that loads once the field is open draws its letters wider or narrower than first measured.
      document.fonts.addEventListener("loadingdone", measureAgain);
      return () => {
        resized.disconnect();
        document.fonts.removeEventListener("loadingdone", measureAgain);
      };
    },
    [span.size, info.width, refocus],
  );
  // A font file dropped on the field is the user's copy of the span's font.
  const { dropProps } = useDrop({
    ref: field,
    onDrop: (event) => {
      const dropped = event.items.filter(isFileDropItem).map((item) => item.getFile());
      void attachDropped(editor, span.font, dropped);
    },
  });
  // Text dragged here is the field's own to take, as typing is: useDrop would swallow it.
  const filesOnly = (handler?: DragEventHandler<HTMLInputElement>) => (event: DragEvent<HTMLInputElement>) => {
    if (carriesFiles(event.dataTransfer)) handler?.(event);
  };

  // Once, as the field takes focus: what the press that opened it selects, or everything from
  // the keyboard. Later focus keeps the caret where the user put it.
  function place(event: FocusEvent<HTMLInputElement>) {
    if (editor.store.get().focusTo !== null) focusMoved(editor);
    if (placed.current) return;
    placed.current = true;
    const input = event.currentTarget;
    const press = editor.store.get().draft?.press;
    if (press == null) input.select();
    else input.setSelectionRange(...selectionAt(start, press, { glyphs, span }));
    fieldFocused(editor);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    // Alt+Down opens a picker, as it does in any field that has one.
    if (event.key === "ArrowDown" && event.altKey && openValueEditor(editor, value)) {
      event.preventDefault();
      return;
    }
    // Up and Down choose how a long line fits; while it fits, they're the field's own.
    const step = { ArrowUp: -1, ArrowDown: 1 } as const;
    if ((event.key === "ArrowUp" || event.key === "ArrowDown") && stepWayOut(editor, step[event.key])) {
      event.preventDefault();
      return;
    }
    if (event.key !== "Enter" && event.key !== "Escape") return;
    event.preventDefault();
    if (event.key === "Enter") enter(editor);
    else finish(editor, false, { returnFocus: true });
  }

  // Its size by CSS variables, which a touch screen's stylesheet sets it from (EditField.module.css).
  const { fontSize, height, ...where } = spanTextStyle(span, info, face);
  return (
    <>
      <TextField
        aria-label={`Change “${start}”`}
        value={text}
        onChange={(next) => typeInto(editor, next)}
        className={styles.field}
      >
        <Input
          {...dropProps}
          onDragEnter={filesOnly(dropProps.onDragEnter)}
          onDragOver={filesOnly(dropProps.onDragOver)}
          onDragLeave={filesOnly(dropProps.onDragLeave)}
          onDrop={filesOnly(dropProps.onDrop)}
          ref={watchPage}
          autoFocus
          onFocus={place}
          className={trouble !== "" ? styles.troubleInput : styles.input}
          spellCheck={false}
          autoComplete="off"
          onKeyDown={onKeyDown}
          // Only leaving the field ends the typing: a file picker takes the window's focus, not the field's,
          // and the value's editor in the slip goes on with it.
          onBlur={(event) => {
            const to = event.relatedTarget;
            if (to instanceof Node && slip.current?.contains(to)) return;
            if (document.hasFocus()) finish(editor, true);
          }}
          style={{
            ...where,
            "--field-size": fontSize,
            "--field-width": points(fieldWidthPt(span, { text, face, glyphs }), info),
            "--field-height": height,
            "--field-scale": scale,
            "--field-shrink": setting.size,
            "--field-narrow": setting.scaleX,
          }}
        />
      </TextField>
      {pastRoom && (
        <span
          className={styles.roomEnds}
          aria-hidden="true"
          style={boxOf({ ...span.bbox, x0: span.bbox.x1 + span.room_pt, x1: span.bbox.x1 + span.room_pt }, info)}
        />
      )}
      {value !== undefined && (
        // The part the value's editor rewrites, ringed as Find rings the place it went to.
        <span
          className={styles.valueRing}
          aria-hidden="true"
          style={boxOf(fieldPartOf(span, { text, start: value.start, end: value.end, face, scale: setting }), info)}
        />
      )}
      <Chip
        slip={slip}
        span={span}
        info={info}
        trouble={trouble}
        verdict={verdict}
        setting={setting}
        value={value}
        field={field}
      />
    </>
  );
}
