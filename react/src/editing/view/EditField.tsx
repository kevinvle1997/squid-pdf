import {
  type DragEvent,
  type DragEventHandler,
  type FocusEvent,
  useCallback,
  useContext,
  useEffect,
  useReducer,
  useRef,
  useState,
} from "react";
import { isFileDropItem, useDrop } from "react-aria";
import { Input, Text, TextField } from "react-aria-components";
import { createPortal } from "react-dom";
import type { PageInfo, SpanInfo } from "../../api/types";
import { selectionAt } from "../caret";
import { carriesFiles } from "../dropped";
import { focusMoved } from "../editor";
import { addFaces, DEFAULT_FACE, previewFaceOf } from "../faces";
import { fitOf, runsPast, settingOf } from "../fit";
import { attachDropped } from "../fonts";
import { editableValueIn } from "../formattedValues";
import { redactedRangesOf, textIn } from "../project";
import { redactedSentences } from "../redaction";
import { fontOf } from "../spanFonts";
import { shallowEqual } from "../store";
import { fieldFocused, spanDraftOf, troublesIn, typeInto } from "../typing";
import { Chip } from "./Chip";
import { SlipLayerContext, useEditor, useEditorState } from "./context";
import styles from "./EditField.module.css";
import { fieldKeyDown } from "./fieldKeys";
import { boxOf, fieldPartOf, fieldScaleOf, fieldWidthPt, points, spanTextStyle } from "./geometry";
import { useLeaveTyping } from "./leaveTyping";

export function EditField({ span, info }: { span: SpanInfo; info: PageInfo }) {
  const editor = useEditor();
  const text = useEditorState((state) => spanDraftOf(state)?.text ?? "");
  const font = useEditorState((state) => fontOf(state.layout.fonts, span));
  const start = useEditorState((state) => textIn(state.reading, span));
  const reading = useEditorState((state) => state.reading.spans.get(span.id));
  const ranges = redactedRangesOf(reading);
  const sentences = useEditorState((state) => state.doc.sentences);
  const face = font === undefined ? DEFAULT_FACE : previewFaceOf(span, { font, text });
  useEffect(() => addFaces([face]), [face]);
  const placed = useRef(false);
  const glyphs = font?.glyphs ?? {};
  const rules = useEditorState((state) => state.doc.fit);
  const asked = useEditorState((state) => spanDraftOf(state)?.strategy);
  const setting = settingOf(span, { font, text, asked, rules });
  const fit = font === undefined ? undefined : fitOf(span, { font, text, rules });
  const pastRoom = fit !== undefined && setting.way === "as-is" && runsPast(fit.deltaPt, fit.roomPt, rules);
  const { trouble, fidelity, why, substitute, sameWidths } = useEditorState((state) => {
    const { sentences, verdict } = troublesIn(state, spanDraftOf(state)?.text ?? "");
    const comesTo = verdict ?? span;
    const { fidelity, why, substitute, same_widths: sameWidths } = comesTo;
    return { trouble: sentences.join("\n"), fidelity, why, substitute, sameWidths };
  }, shallowEqual);
  const verdict = { fidelity, why, substitute, same_widths: sameWidths };
  const press = useEditorState((state) => spanDraftOf(state)?.press ?? null);
  const dates = useEditorState((state) => state.dates);
  const held = useEditorState((state) => spanDraftOf(state)?.value ?? null);
  const at = press === null ? undefined : selectionAt(start, press, { glyphs, span })[0];
  const value = ranges.length > 0 ? undefined : editableValueIn(text, { dates, at, held });
  const refocus = useEditorState((state) => {
    const { focusTo } = state;
    return focusTo !== null && "spanId" in focusTo && focusTo.spanId === span.id ? focusTo : null;
  });
  const field = useRef<HTMLInputElement>(null);
  const chip = useRef<HTMLDivElement>(null);
  const slipLayer = useContext(SlipLayerContext);
  const [scale, setScale] = useState(1);
  const [, measureAgain] = useReducer((times: number) => times + 1, 0);
  const leave = useLeaveTyping();
  const watchPage = useCallback(
    (input: HTMLInputElement | null) => {
      field.current = input;
      if (refocus !== null && input !== null) {
        // Sent back while it has focus already: no focus event comes to take it, so it's taken here.
        if (input === document.activeElement) focusMoved(editor);
        else input.focus();
      }
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
    [span.size, info.width, refocus, editor],
  );
  const { dropProps } = useDrop({
    ref: field,
    onDrop: (event) => {
      const dropped = event.items.filter(isFileDropItem).map((item) => item.getFile());
      void attachDropped(editor, span.font, dropped);
    },
  });
  const filesOnly = (handler?: DragEventHandler<HTMLInputElement>) => (event: DragEvent<HTMLInputElement>) => {
    if (carriesFiles(event.dataTransfer)) handler?.(event);
  };

  function place(event: FocusEvent<HTMLInputElement>) {
    if (editor.store.get().focusTo !== null) focusMoved(editor);
    if (placed.current) return;
    placed.current = true;
    const input = event.currentTarget;
    const draft = spanDraftOf(editor.store.get());
    const press = draft?.press;
    if (draft?.caret != null) input.setSelectionRange(draft.caret, draft.caret);
    else if (press == null) input.select();
    else input.setSelectionRange(...selectionAt(start, press, { glyphs, span }));
    fieldFocused(editor);
  }

  const { fontSize, height, ...where } = spanTextStyle(span, info, face);
  const widthPt = fieldWidthPt(span, { text, face, glyphs });
  return (
    <>
      <TextField
        aria-label={`Change “${start}”`}
        value={text}
        onChange={(next) => typeInto(editor, next, { caret: field.current?.selectionStart ?? null })}
        className={styles.field}
      >
        <Input
          {...dropProps}
          onDragEnter={filesOnly(dropProps.onDragEnter)}
          onDragOver={filesOnly(dropProps.onDragOver)}
          onDragLeave={filesOnly(dropProps.onDragLeave)}
          onDrop={filesOnly(dropProps.onDrop)}
          ref={watchPage}
          data-span-field=""
          autoFocus
          onFocus={place}
          className={trouble !== "" ? styles.troubleInput : styles.input}
          spellCheck={false}
          autoComplete="off"
          onKeyDown={(event) => fieldKeyDown(editor, event, { value })}
          onBlur={(event) => {
            const to = event.relatedTarget;
            if (to instanceof Node && chip.current?.contains(to)) return;
            leave(event);
          }}
          style={{
            ...where,
            "--field-size": fontSize,
            "--field-width": points(widthPt, info),
            "--field-height": height,
            "--field-scale": scale,
            "--field-shrink": setting.size,
            "--field-narrow": setting.scaleX,
          }}
        />
        {ranges.length > 0 && (
          <Text slot="description" className="vh">
            {redactedSentences(sentences, { text: span.text, ranges })}
          </Text>
        )}
      </TextField>
      {pastRoom && (
        <span
          className={styles.roomEnds}
          aria-hidden="true"
          style={boxOf({ ...span.bbox, x0: span.bbox.x1 + span.room_pt, x1: span.bbox.x1 + span.room_pt }, info)}
        />
      )}
      {ranges.map((range) => (
        <span
          key={range.start}
          className={styles.redactedRun}
          aria-hidden="true"
          style={boxOf(fieldPartOf(span, { text, ...range, face, scale: setting }), info)}
        />
      ))}
      {value !== undefined && (
        <span
          className={styles.valueRing}
          aria-hidden="true"
          style={boxOf(fieldPartOf(span, { text, start: value.start, end: value.end, face, scale: setting }), info)}
        />
      )}
      {slipLayer !== null &&
        createPortal(
          <Chip
            chip={chip}
            span={span}
            fieldEndPt={span.bbox.x0 + widthPt}
            info={info}
            trouble={trouble}
            verdict={verdict}
            setting={setting}
            value={value}
            field={field}
          />,
          slipLayer,
        )}
    </>
  );
}
