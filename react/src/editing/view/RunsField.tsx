import {
  type DragEvent,
  type DragEventHandler,
  type FocusEvent,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { isFileDropItem, useDrop } from "react-aria";
import { createPortal, flushSync } from "react-dom";
import type { PageInfo } from "../../api/types";
import { selectionAcross } from "../caret";
import { carriesFiles } from "../dropped";
import { focusMoved } from "../editor";
import { addFaces, DEFAULT_FACE, previewFaceOf } from "../faces";
import { lineFitOf, runsPast, type Setting, setAs, wayDrawn, waysOffered } from "../fit";
import { attachDropped } from "../fonts";
import { caretRunValueIn, type EditableValue, lineValueIn, runExtents } from "../formattedValues";
import { deletedBy, type Line, lineDrawnAs, rulesFor, runsEdited } from "../lines";
import { textIn } from "../project";
import { fontOf } from "../spanFonts";
import { shallowEqual } from "../store";
import { asOneLine, fieldFocused, lineNoteOf, spanDraftOf, stepTyping, troublesIn, typeIntoRuns } from "../typing";
import { Chip } from "./Chip";
import { SlipLayerContext, useEditor, useEditorState } from "./context";
import { fieldKeyDown, typingStepOf } from "./fieldKeys";
import { boxOf, fieldPartOf, fieldScaleOf, points, runTextStyle } from "./geometry";
import { useLeaveTyping } from "./leaveTyping";
import {
  rangeEditedIn,
  runsIntact,
  runsWordsIn,
  runUnder,
  selectIn,
  selectionIn,
  takesPlainTextOnly,
} from "./lineSelection";
import styles from "./RunsField.module.css";

const NO_RUNS: readonly string[] = [];
const AS_SET = { size: 1, scaleX: 1 };

// Plain text alone where the browser can hold an editable box to it; elsewhere a paste's markup is refused by hand.
const EDITABLE = takesPlainTextOnly() ? "plaintext-only" : "true";

export function RunsField({ line, info }: { line: Line; info: PageInfo }) {
  const editor = useEditor();
  const runs = useEditorState((state) => spanDraftOf(state)?.runs ?? NO_RUNS);
  const fonts = useEditorState((state) => state.layout.fonts);
  const start = useEditorState((state) => line.spans.map((span) => textIn(state.reading, span)).join(""));
  const documentRules = useEditorState((state) => state.doc.fit);
  const rules = rulesFor(line, documentRules);
  const asked = useEditorState((state) => spanDraftOf(state)?.strategy);
  const typed = line.spans.flatMap((span, index) => {
    const font = fontOf(fonts, span);
    return font === undefined ? [] : [{ span, font, text: runs[index] ?? "" }];
  });
  const fit = typed.length === line.spans.length ? lineFitOf(typed, { roomPt: line.roomPt, rules }) : undefined;
  const way = fit === undefined ? "as-is" : wayDrawn(asked, fit);
  const setting: Setting = {
    way,
    ...(fit === undefined ? AS_SET : setAs(way, fit)),
    offered: fit ? waysOffered(fit) : [],
  };
  const pastRoom = fit !== undefined && way === "as-is" && runsPast(fit.deltaPt, fit.roomPt, rules);
  const faces = line.spans.map((span, index) => {
    const font = fontOf(fonts, span);
    return font === undefined ? DEFAULT_FACE : previewFaceOf(span, { font, text: runs[index] ?? "" });
  });
  const facesNeeded = faces.join("\n");
  useEffect(() => addFaces(facesNeeded.split("\n")), [facesNeeded]);
  const { trouble, fidelity, why, substitute, sameWidths } = useEditorState((state) => {
    const { sentences, verdict } = troublesIn(state, spanDraftOf(state)?.runs ?? NO_RUNS);
    const comesTo = verdict ?? line.spans[0];
    return {
      trouble: sentences.join("\n"),
      fidelity: comesTo?.fidelity ?? "exact",
      why: comesTo?.why ?? null,
      substitute: comesTo?.substitute ?? null,
      sameWidths: comesTo?.same_widths ?? false,
    };
  }, shallowEqual);
  const verdict = { fidelity, why, substitute, same_widths: sameWidths };
  const warned = useEditorState((state) => {
    const { note, fonts: warnedFonts } = lineNoteOf(state, spanDraftOf(state)?.runs ?? NO_RUNS);
    return { sentence: note?.sentence ?? null, why: note?.why ?? null, fonts: warnedFonts.join("\n") };
  }, shallowEqual);
  const press = useEditorState((state) => spanDraftOf(state)?.press ?? null);
  const dates = useEditorState((state) => state.dates);
  const held = useEditorState((state) => spanDraftOf(state)?.value ?? null);
  const redacted = useEditorState((state) =>
    line.spans.some(({ id }) => state.reading.spans.get(id)?.redacted != null),
  );
  const at = press === null ? undefined : selectionAcross(measuresOf(), press)[0];
  const value = redacted ? undefined : lineValueIn(runs, { dates, at, held });
  const refocus = useEditorState((state) => {
    const { focusTo } = state;
    return focusTo !== null && "spanId" in focusTo && focusTo.spanId === line.id ? focusTo : null;
  });
  const selected = useRef<readonly [number, number]>([0, 0]);
  const composedOver = useRef<readonly [number, number]>([0, 0]);
  const [drawing, setDrawing] = useState(0);
  const field = useRef<HTMLDivElement>(null);
  const chip = useRef<HTMLDivElement>(null);
  const placed = useRef(false);
  const slipLayer = useContext(SlipLayerContext);
  const [scale, setScale] = useState(1);
  const basePt = line.spans[0]?.size ?? 1;
  const leave = useLeaveTyping();

  const stepField = useCallback(
    (element: HTMLElement, step: "undo" | "redo") => {
      const selected = selectionIn(element) ?? ([0, 0] as const);
      let to: readonly [number, number] | null = null;
      flushSync(() => {
        to = stepTyping(editor, step, { selected });
      });
      const stepped = to as readonly [number, number] | null;
      if (stepped !== null) selectIn(element, ...stepped);
    },
    [editor],
  );

  const { dropProps } = useDrop({
    ref: field,
    onDrop: (event) => {
      const element = field.current;
      const font = element === null ? undefined : line.spans[runUnder(element, event.x)]?.font;
      const dropped = event.items.filter(isFileDropItem).map((item) => item.getFile());
      if (font !== undefined) void attachDropped(editor, font, dropped);
    },
  });
  const filesOnly = (handler?: DragEventHandler<HTMLDivElement>) => (event: DragEvent<HTMLDivElement>) => {
    if (carriesFiles(event.dataTransfer)) handler?.(event);
  };

  const beforeInput = useCallback(
    (event: InputEvent) => {
      const target = event.currentTarget;
      if (!(target instanceof HTMLElement) || event.inputType === "insertCompositionText") return;
      event.preventDefault();
      if (event.inputType === "insertParagraph" || event.inputType === "insertLineBreak") return;
      if (event.inputType === "historyUndo" || event.inputType === "historyRedo") {
        stepField(target, event.inputType === "historyUndo" ? "undo" : "redo");
        return;
      }
      const draft = spanDraftOf(editor.store.get());
      const edited = rangeEditedIn(target, event);
      if (draft?.runs == null || edited === null) return;
      const inserts = event.inputType.startsWith("insert");
      const words = inserts ? asOneLine(event.data ?? event.dataTransfer?.getData("text/plain") ?? "") : "";
      // A delete with only a caret names no range in every browser: what its key takes is worked out here.
      const [from, to] = inserts ? edited : deletedBy(event.inputType, draft.runs.join(""), edited);
      const caret = from + words.length;
      const made = { selected: edited, typed: words };
      const runs = runsEdited(draft.runs, { start: from, end: to, typed: words });
      flushSync(() => typeIntoRuns(editor, runs, { caret, made }));
      selectIn(target, caret, caret);
    },
    [editor, stepField],
  );

  const watch = useCallback(
    (element: HTMLDivElement | null) => {
      field.current = element;
      if (element === null) return;
      element.addEventListener("beforeinput", beforeInput);
      if (refocus === null && !placed.current) element.focus();
      if (refocus !== null && element === document.activeElement) focusMoved(editor);
      if (refocus !== null && element !== document.activeElement) {
        element.focus();
        selectIn(element, ...selected.current);
      }
      const layer = element.offsetParent;
      const resized = new ResizeObserver(() => {
        const fontPx = parseFloat(getComputedStyle(element).fontSize);
        const shownPx = layer?.clientWidth ?? 0;
        setScale(fieldScaleOf(basePt, { pageWidthPt: info.width, shownPx, fontPx }));
      });
      if (layer instanceof HTMLElement) resized.observe(layer);
      return () => {
        resized.disconnect();
        element.removeEventListener("beforeinput", beforeInput);
      };
    },
    [beforeInput, basePt, info.width, refocus, editor],
  );

  function place(event: FocusEvent<HTMLDivElement>) {
    if (editor.store.get().focusTo !== null) focusMoved(editor);
    if (placed.current) return;
    placed.current = true;
    const element = event.currentTarget;
    const draft = spanDraftOf(editor.store.get());
    const press = draft?.press;
    if (draft?.caret != null) selectIn(element, draft.caret, draft.caret);
    else if (press == null) selectIn(element, 0, start.length);
    else selectIn(element, ...selectionAcross(measuresOf(), press));
    fieldFocused(editor);
  }

  function measuresOf() {
    const [first] = line.spans;
    return line.spans.flatMap((span) => {
      const font = fontOf(fonts, span);
      const text = textIn(editor.store.get().reading, span);
      const startPt = span.bbox.x0 - (first?.bbox.x0 ?? span.bbox.x0);
      return font === undefined ? [] : [{ startPt, text, glyphs: font.glyphs, span }];
    });
  }

  function composed(words: string) {
    const element = field.current;
    const before = spanDraftOf(editor.store.get())?.runs;
    if (element === null || before == null) return;
    const made = { selected: composedOver.current, typed: words };
    if (runsIntact(element, line.spans.length)) {
      const caret = selectionIn(element)?.[1] ?? null;
      flushSync(() => typeIntoRuns(editor, runsWordsIn(element, line.spans.length), { caret, made }));
      if (caret !== null) selectIn(element, caret, caret);
      return;
    }
    const [start, end] = composedOver.current;
    const caret = start + words.length;
    flushSync(() => {
      typeIntoRuns(editor, runsEdited(before, { start, end, typed: words }), { caret, made });
      setDrawing((drawn) => drawn + 1);
    });
    field.current?.focus();
    if (field.current !== null) selectIn(field.current, caret, caret);
  }

  const ring = value === undefined ? undefined : ringOf(value);

  function ringOf(shown: EditableValue) {
    const index = runExtents(runs).findIndex(({ start, end }) => start <= shown.start && shown.end <= end);
    const drawnAs = lineDrawnAs(line, { texts: runs, fonts, asked, rules })?.[index];
    const start = runExtents(runs)[index]?.start ?? 0;
    if (drawnAs === undefined) return undefined;
    const part = { text: drawnAs.text, start: shown.start - start, end: shown.end - start };
    return fieldPartOf(drawnAs.moved, { ...part, face: faces[index] ?? DEFAULT_FACE, scale: drawnAs.scale });
  }

  const box = boxOf(line.bbox, info);
  const widthPt = line.bbox.x1 - line.bbox.x0 + Math.max(fit?.deltaPt ?? 0, 0);
  return (
    <>
      {/* biome-ignore lint/a11y/useSemanticElements: see `role` below */}
      <div
        key={drawing}
        {...dropProps}
        onDragEnter={filesOnly(dropProps.onDragEnter)}
        onDragOver={filesOnly(dropProps.onDragOver)}
        onDragLeave={filesOnly(dropProps.onDragLeave)}
        onDrop={filesOnly(dropProps.onDrop)}
        ref={watch}
        // Hand-rolled because React Aria has no editable-text field: an input holds one font, and each run keeps its own.
        role="textbox"
        tabIndex={0}
        aria-label={`Change “${start}”`}
        aria-multiline="false"
        contentEditable={EDITABLE}
        suppressContentEditableWarning
        spellCheck={false}
        autoCorrect="off"
        autoCapitalize="off"
        data-span-field=""
        className={trouble !== "" ? styles.troubleField : styles.field}
        onFocus={place}
        onKeyDown={(event) => {
          const step = typingStepOf(event);
          if (step === null) {
            const caret = selectionIn(event.currentTarget)?.[0] ?? 0;
            const caretValue = event.altKey ? caretRunValueIn(runs, { dates, caret }) : value;
            fieldKeyDown(editor, event, { value: redacted ? undefined : caretValue });
            return;
          }
          event.preventDefault();
          stepField(event.currentTarget, step);
        }}
        onCompositionStart={(event) => {
          composedOver.current = selectionIn(event.currentTarget) ?? composedOver.current;
        }}
        onCompositionEnd={(event) => composed(event.data)}
        onBlur={(event) => {
          selected.current = selectionIn(event.currentTarget) ?? selected.current;
          const to = event.relatedTarget;
          if (to instanceof Node && chip.current?.contains(to)) return;
          leave(event);
        }}
        style={{
          left: box.left,
          top: box.top,
          "--field-size": points(basePt, info),
          "--field-width": points(line.bbox.x1 - line.bbox.x0, info),
          "--field-height": points(line.bbox.y1 - line.bbox.y0, info),
          "--field-scale": scale,
        }}
      >
        {line.spans.map((span, index) => {
          const text = runs[index] ?? "";
          const previous = line.spans[index - 1];
          const changed = text !== span.text;
          return (
            <span
              key={span.id}
              data-run={index}
              style={runTextStyle(span, {
                basePt,
                face: faces[index] ?? DEFAULT_FACE,
                text,
                gapPt: previous === undefined ? 0 : span.bbox.x0 - previous.bbox.x1,
                scale: changed ? setting : AS_SET,
              })}
            >
              {text}
            </span>
          );
        })}
      </div>
      {pastRoom && (
        <span
          className={styles.roomEnds}
          aria-hidden="true"
          style={boxOf({ ...line.bbox, x0: line.bbox.x1 + line.roomPt, x1: line.bbox.x1 + line.roomPt }, info)}
        />
      )}
      {ring !== undefined && <span className={styles.valueRing} aria-hidden="true" style={boxOf(ring, info)} />}
      {slipLayer !== null &&
        line.spans[0] !== undefined &&
        createPortal(
          <Chip
            chip={chip}
            span={line.spans[0]}
            fieldEndPt={line.bbox.x0 + widthPt}
            info={info}
            trouble={trouble}
            verdict={verdict}
            setting={setting}
            value={value}
            field={field}
            lineNote={{
              note: warned.sentence === null ? null : { warn: true, sentence: warned.sentence, why: warned.why },
              fonts: warned.fonts === "" ? [] : warned.fonts.split("\n"),
            }}
          />,
          slipLayer,
        )}
    </>
  );
}
