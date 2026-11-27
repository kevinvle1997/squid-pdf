import { type FocusEvent, useCallback, useContext, useEffect, useRef } from "react";
import { createPortal, flushSync } from "react-dom";
import type { PageInfo } from "../../api/types";
import { selectionAcross } from "../caret";
import { focusMoved } from "../editor";
import { addFaces, DEFAULT_FACE, previewFaceOf } from "../faces";
import { lineFitOf, runsPast, type Setting, setAs, wayDrawn, waysOffered } from "../fit";
import { deletedBy, type Line, runsEdited } from "../lines";
import { textIn } from "../project";
import { fontOf } from "../spanFonts";
import { shallowEqual } from "../store";
import { asOneLine, fieldFocused, lineNoteOf, spanDraftOf, troublesIn, typeIntoRuns } from "../typing";
import { Chip } from "./Chip";
import { SlipLayerContext, useEditor, useEditorState } from "./context";
import { fieldKeyDown } from "./fieldKeys";
import { boxOf, points, runTextStyle } from "./geometry";
import { useLeaveTyping } from "./leaveTyping";
import { rangeEditedIn, runsWordsIn, selectIn, selectionIn, takesPlainTextOnly } from "./lineSelection";
import styles from "./RunsField.module.css";

const NO_RUNS: readonly string[] = [];
const AS_SET = { size: 1, scaleX: 1 };

// Plain text alone where the browser can hold an editable box to it; elsewhere a paste's markup is refused by hand.
const EDITABLE = takesPlainTextOnly() ? "plaintext-only" : "true";

/**
 * Typing in place into a line of several runs: each run in the face and size that will draw it, the words typed going
 * into the run they're typed in. The browser makes no change itself: each goes through the line's runs, and the
 * caret is put back after it. It stands where the line's mark stood, so Tab goes on to the next line unaided.
 */
export function RunsField({ line, info }: { line: Line; info: PageInfo }) {
  const editor = useEditor();
  const runs = useEditorState((state) => spanDraftOf(state)?.runs ?? NO_RUNS);
  const fonts = useEditorState((state) => state.layout.fonts);
  const start = useEditorState((state) => line.spans.map((span) => textIn(state.reading, span)).join(""));
  const rules = useEditorState((state) => state.doc.fit);
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
  // Left long, a line past its room shows where the room ends: what to shorten it to.
  const pastRoom = fit !== undefined && way === "as-is" && runsPast(fit.deltaPt, fit.roomPt, rules);
  const faces = line.spans.map((span, index) => {
    const font = fontOf(fonts, span);
    return font === undefined ? DEFAULT_FACE : previewFaceOf(span, { font, text: runs[index] ?? "" });
  });
  const facesNeeded = faces.join("\n");
  // A letter a look-alike lacks sends its run to the broadest face, put on the page only once one does.
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
  const field = useRef<HTMLDivElement>(null);
  const chip = useRef<HTMLDivElement>(null);
  const placed = useRef(false);
  const slipLayer = useContext(SlipLayerContext);
  const leave = useLeaveTyping();

  // Each change the browser would make goes through the line's runs instead, and the caret goes where it ends.
  const beforeInput = useCallback(
    (event: InputEvent) => {
      const target = event.currentTarget;
      // An input method's words go in as it composes them, read back once it ends.
      if (!(target instanceof HTMLElement) || event.inputType === "insertCompositionText") return;
      event.preventDefault();
      // One line: a break is nothing here, and Enter keeps the edit (`fieldKeyDown`).
      if (event.inputType === "insertParagraph" || event.inputType === "insertLineBreak") return;
      // The browser's own undo would undo words it never typed: Escape puts the line back instead.
      if (event.inputType.startsWith("history")) return;
      const draft = spanDraftOf(editor.store.get());
      const edited = rangeEditedIn(target, event);
      if (draft?.runs == null || edited === null) return;
      const inserts = event.inputType.startsWith("insert");
      const words = inserts ? asOneLine(event.data ?? event.dataTransfer?.getData("text/plain") ?? "") : "";
      // A delete with only a caret names no range in every browser: what its key takes is worked out here.
      const [from, to] = inserts ? edited : deletedBy(event.inputType, draft.runs.join(""), edited);
      const caret = from + words.length;
      flushSync(() =>
        typeIntoRuns(editor, runsEdited(draft.runs ?? [], { start: from, end: to, typed: words }), { caret }),
      );
      selectIn(target, caret, caret);
    },
    [editor],
  );

  // The field as it's drawn: it hears every change before the browser makes it, and takes focus at once.
  const watch = useCallback(
    (element: HTMLDivElement | null) => {
      field.current = element;
      if (element === null) return;
      element.addEventListener("beforeinput", beforeInput);
      element.focus();
      return () => element.removeEventListener("beforeinput", beforeInput);
    },
    [beforeInput],
  );

  // Once, as the field takes focus: what the press that opened it selects, or everything from the keyboard.
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

  // Each run where its span starts on the line, set as it reads as the field opens, for a press to find its caret in.
  function measuresOf() {
    const [first] = line.spans;
    return line.spans.flatMap((span) => {
      const font = fontOf(fonts, span);
      const text = textIn(editor.store.get().reading, span);
      const startPt = span.bbox.x0 - (first?.bbox.x0 ?? span.bbox.x0);
      return font === undefined ? [] : [{ startPt, text, glyphs: font.glyphs, span }];
    });
  }

  // An input method's words, once it's done: read back from the runs it typed into.
  function composed() {
    const element = field.current;
    if (element === null) return;
    const caret = selectionIn(element)?.[1] ?? null;
    flushSync(() => typeIntoRuns(editor, runsWordsIn(element, line.spans.length), { caret }));
    if (caret !== null) selectIn(element, caret, caret);
  }

  const box = boxOf(line.bbox, info);
  const widthPt = line.bbox.x1 - line.bbox.x0 + Math.max(fit?.deltaPt ?? 0, 0);
  return (
    <>
      {/* biome-ignore lint/a11y/useSemanticElements: an input holds one font; a line's runs each keep their own */}
      <div
        ref={watch}
        role="textbox"
        tabIndex={0}
        aria-label={`Change “${start}”`}
        aria-multiline="false"
        contentEditable={EDITABLE}
        suppressContentEditableWarning
        spellCheck={false}
        autoCorrect="off"
        autoCapitalize="off"
        // A key pressed over words selected here may redact them (`selectedIn`).
        data-span-field=""
        className={trouble !== "" ? styles.troubleField : styles.field}
        onFocus={place}
        onKeyDown={(event) => fieldKeyDown(editor, event, { value: undefined })}
        onCompositionEnd={composed}
        // Only leaving the field ends the typing: the slip goes on with it.
        onBlur={(event) => {
          const to = event.relatedTarget;
          if (to instanceof Node && chip.current?.contains(to)) return;
          leave(event);
        }}
        style={{
          left: box.left,
          top: box.top,
          minWidth: box.width,
          height: box.height,
          lineHeight: points(line.bbox.y1 - line.bbox.y0, info),
        }}
      >
        {line.spans.map((span, index) => {
          const text = runs[index] ?? "";
          const previous = line.spans[index - 1];
          const changed = text !== span.text;
          return (
            <span
              // By its place in the line: a run's span never moves.
              key={span.id}
              data-run={index}
              style={runTextStyle(span, info, {
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
            value={undefined}
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
