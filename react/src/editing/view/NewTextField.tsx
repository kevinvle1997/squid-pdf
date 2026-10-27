import { type KeyboardEvent, useCallback, useEffect, useReducer, useRef, useState } from "react";
import { useFocusWithin } from "react-aria";
import { Group, Input, TextField } from "react-aria-components";
import type { PageInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { ChoiceList } from "../../ui/ChoiceList";
import { Warn } from "../../ui/Warn";
import { isUndoOrRedo } from "../commands";
import { HANDWRITING_STEP, SLIP_GAP_PT } from "../constants";
import { addFaces, familyOf } from "../faces";
import { readableName } from "../fonts";
import { setWarningMuted } from "../mutedWarnings";
import {
  chooseFace,
  chosenFaceOf,
  faceChoicesOf,
  fontNameOf,
  fontOfText,
  inShippedFace,
  insertOf,
  type TextDraft,
  textFaceOf,
  textSpanOf,
} from "../newText";
import { shownNameIn } from "../spanFonts";
import { shallowEqual } from "../store";
import { draftNoteOf, enter, fieldFocused, finish, troublesIn, typeIntoText } from "../typing";
import { shortWarningOf } from "../verdicts";
import { Facts } from "./Chip";
import styles from "./Chip.module.css";
import { useEditor, useEditorState } from "./context";
import fieldStyles from "./EditField.module.css";
import { fieldScaleOf, points, textBoxOf, textStyleOf, textWidthIn, textWidthPt } from "./geometry";

/**
 * New text being typed, where it will be drawn, in the face that will draw it, and its slip under it: what it
 * comes to (Rule 1, as an edit of a span), then the faces it can be set in. Leaving both ends the typing.
 */
export function NewTextField({ info }: { info: PageInfo }) {
  const editor = useEditor();
  const draft = useEditorState((state) => (state.draft?.kind === "text" ? state.draft : null));
  const layout = useEditorState((state) => state.layout);
  const field = useRef<HTMLInputElement>(null);
  const placed = useRef(false);
  const [, measureAgain] = useReducer((times: number) => times + 1, 0);
  const [scale, setScale] = useState(1);
  const size = draft?.style.size ?? 0;
  // As the page is shown wider or narrower, what a touch screen's field scales by to show the text's size.
  const watchPage = useCallback(
    (input: HTMLInputElement | null) => {
      field.current = input;
      const layer = input?.offsetParent;
      if (input == null || !(layer instanceof HTMLElement)) return;
      const resized = new ResizeObserver(() => {
        const fontPx = parseFloat(getComputedStyle(input).fontSize);
        setScale(fieldScaleOf(size, { pageWidthPt: info.width, shownPx: layer.clientWidth, fontPx }));
      });
      resized.observe(layer);
      return () => resized.disconnect();
    },
    [size, info.width],
  );
  const insert = draft === null ? null : insertOf(draft);
  const face = insert === null ? null : textFaceOf(layout, insert);
  // A letter the face lacks sends the line to its broadest, put on the page only once one does.
  useEffect(() => {
    if (face === null) return;
    addFaces([face]);
    // A face that loads once the field is open draws its letters wider or narrower than first measured.
    document.fonts.addEventListener("loadingdone", measureAgain);
    return () => document.fonts.removeEventListener("loadingdone", measureAgain);
  }, [face]);
  const { focusWithinProps } = useFocusWithin({
    // Only leaving the field and its slip ends the typing: a file picker takes the window's focus, not theirs.
    onBlurWithin: () => {
      if (document.hasFocus()) finish(editor, true);
    },
  });
  if (draft === null || insert === null || face === null) return null;

  // Once, as the field takes focus: what's here already is heard, and what an edit in its font warns of.
  function place() {
    if (placed.current) return;
    placed.current = true;
    fieldFocused(editor);
  }

  function onKeyDown(event: KeyboardEvent) {
    // In the slip, undo and redo are the typing's, as in a span's calendar: nothing is undone, the field's words among it.
    if (event.target !== field.current && isUndoOrRedo(event)) {
      event.preventDefault();
      return;
    }
    if (event.key !== "Escape" && !(event.key === "Enter" && event.target === field.current)) return;
    event.preventDefault();
    if (event.key === "Enter") enter(editor);
    else finish(editor, false, { returnFocus: true });
  }

  const font = fontOfText(layout, insert);
  const widthPt = textWidthPt(insert, { face, glyphs: font?.glyphs ?? {} });
  // Never narrower than a few letters, with room past what's typed for the caret.
  const fieldWidthPt = Math.max(widthPt, insert.size * 2) + insert.size * 0.5;
  // The slip hangs under the line as a handwriting face sets it, the larger: choosing a face never moves it from under the pointer.
  const box = textBoxOf(
    { ...insert, size: Math.max(insert.size, draft.style.nearSize * HANDWRITING_STEP) },
    fieldWidthPt,
  );
  // Its size by CSS variables, which a touch screen's stylesheet sets it from (EditField.module.css).
  const { fontSize, height, ...where } = textStyleOf(insert, info, face);
  return (
    // `data-slip`: a key pressed in the slip is the slip's, as one in the field is the field's (`isTyping`).
    <Group
      onFocus={focusWithinProps.onFocus}
      onBlur={focusWithinProps.onBlur}
      aria-label="New text and its font"
      className={fieldStyles.field}
      onKeyDown={onKeyDown}
      data-slip=""
    >
      <TextField
        aria-label={draft.of === null ? "New text" : `Change “${draft.of.edit.text}”`}
        value={draft.text}
        onChange={(next) => typeIntoText(editor, next, { widthOf: (typed) => textWidthIn(layout, typed) })}
        className={fieldStyles.field}
      >
        <Input
          ref={watchPage}
          autoFocus
          onFocus={place}
          className={fieldStyles.input}
          spellCheck={false}
          autoComplete="off"
          style={{
            ...where,
            "--field-size": fontSize,
            "--field-width": points(fieldWidthPt, info),
            "--field-height": height,
            "--field-scale": scale,
          }}
        />
      </TextField>
      <TextChip draft={draft} info={info} box={box} onPointerChoice={() => field.current?.focus()} />
    </Group>
  );
}

/** The slip under new text: what it comes to, the full warning as it opens, and the faces it can be set in. */
function TextChip({
  draft,
  info,
  box,
  onPointerChoice,
}: {
  draft: TextDraft;
  info: PageInfo;
  box: { x0: number; y0: number; x1: number; y1: number };
  onPointerChoice: () => void;
}) {
  const editor = useEditor();
  const kept = useKeptOnPage(box, info);
  const { trouble, note, why, shortWarning, drawnIn } = useEditorState((state) => {
    const { sentences, verdict } = troublesIn(state, draft.text);
    const insert = insertOf(draft);
    const said = verdict === null ? null : draftNoteOf(state, verdict);
    const shown = readableName(shownNameIn(state.layout.fonts, textSpanOf(state.layout, insert)));
    const named = inShippedFace(insert) ? fontNameOf(insert).replace(/ Regular$/, "") : shown;
    return {
      trouble: sentences.join("\n"),
      note: said?.sentence ?? null,
      why: said?.why ?? null,
      shortWarning: verdict === null ? null : shortWarningOf(verdict),
      drawnIn: verdict?.substitute ?? named,
    };
  }, shallowEqual);
  const shown = useEditorState((state) => shownNameIn(state.layout.fonts, textSpanOf(state.layout, insertOf(draft))));
  const choices = useEditorState((state) => faceChoicesOf(state, draft), sameChoices);
  const chosen = useEditorState((state) => chosenFaceOf(state.layout, draft));
  const failed = useEditorState((state) => state.shippedFaces.failed);
  const layout = useEditorState((state) => state.layout);
  // Each name is set in its own face: put on the page as the list shows.
  useEffect(() => addFaces([...choices.page, ...choices.handwriting].map((choice) => choice.face)), [choices]);
  const named = (choice: { key: string; label: string; face: string }) => ({
    id: choice.key,
    label: choice.label,
    style: { fontFamily: familyOf(choice.face) },
  });
  const groups = [
    { name: "This page's fonts", shown: false, choices: choices.page.map(named) },
    ...(choices.handwriting.length > 0
      ? [{ name: "Handwriting", shown: true, choices: choices.handwriting.map(named) }]
      : []),
  ];
  return (
    <div ref={kept.ref} className={styles.chip} style={kept.style}>
      {note !== null && (
        // Heard as the field takes focus; its button is a pointer's, the keyboard's way is the Fonts list.
        <span className={styles.note}>
          <span className={styles.noteWords} aria-hidden="true">
            <Warn mark>{note}</Warn>
            {why !== null && <span>{why}</span>}
          </span>
          <Button
            variant="quiet"
            className={styles.muteWarning}
            keepFocus
            onPress={() => setWarningMuted(editor, shown, { muted: true })}
          >
            Don't show again for this font
          </Button>
        </span>
      )}
      <Facts trouble={trouble} shortWarning={shortWarning} drawnIn={drawnIn} sizePt={draft.style.size} />
      <span className={styles.faces}>
        <ChoiceList
          label="Font"
          groups={groups}
          chosen={chosen}
          onChoose={(key) => chooseFace(editor, key, { widthOf: (insert) => textWidthIn(layout, insert) })}
          onEnter={() => enter(editor)}
          onPointerChoice={onPointerChoice}
        />
        {failed !== null && <span className={styles.facesFailed}>{failed}</span>}
      </span>
    </div>
  );
}

/**
 * Where the slip goes so the page holds all of it, as the page clips what runs past its edges: under the text, slid
 * left as far as it would run past the right edge; over it, where there's room there and none under it; and where
 * there's room for it on neither side, on the roomier one, as tall as that room, scrolled.
 */
function useKeptOnPage(box: { x0: number; y0: number; x1: number; y1: number }, info: PageInfo) {
  const [kept, setKept] = useState<{ slidPx: number; above: boolean; roomPx: number | null }>({
    slidPx: 0,
    above: false,
    roomPx: null,
  });
  const { x0, y0, y1 } = box;
  const ref = useCallback(
    (slip: HTMLDivElement | null) => {
      const layer = slip?.offsetParent;
      if (slip == null || !(layer instanceof HTMLElement)) return;
      const measured = new ResizeObserver(() => {
        // Its own offsets and full height, which a transform and a scroll leave alone.
        const pxPerPt = layer.clientWidth / info.width;
        const left = x0 * pxPerPt;
        const runsPast = left + slip.offsetWidth - layer.clientWidth;
        const below = layer.clientHeight - (y1 + SLIP_GAP_PT) * pxPerPt;
        const above = (y0 - SLIP_GAP_PT) * pxPerPt;
        const tall = slip.scrollHeight;
        const goesAbove = tall > below && above > below;
        const room = goesAbove ? above : below;
        setKept({
          slidPx: runsPast > 0 ? Math.min(runsPast, left) : 0,
          above: goesAbove,
          roomPx: tall > room ? room : null,
        });
      });
      measured.observe(slip);
      measured.observe(layer);
      return () => measured.disconnect();
    },
    [x0, y0, y1, info.width],
  );
  const placed = kept.above
    ? { bottom: points(info.height - y0 + SLIP_GAP_PT, info), left: points(x0, info) }
    : { top: points(y1 + SLIP_GAP_PT, info), left: points(x0, info) };
  const scrolled =
    kept.roomPx === null
      ? {}
      : { maxHeight: `${kept.roomPx}px`, overflowY: "auto" as const, pointerEvents: "auto" as const };
  return { ref, style: { ...placed, ...scrolled, translate: `${-kept.slidPx}px 0` } };
}

/** Whether two lists of faces offer the same, by key and face: each is built fresh from the state. */
function sameChoices(a: ReturnType<typeof faceChoicesOf>, b: ReturnType<typeof faceChoicesOf>): boolean {
  const same = (x: readonly { key: string; face: string }[], y: readonly { key: string; face: string }[]) =>
    x.length === y.length && x.every((choice, index) => choice.key === y[index]?.key && choice.face === y[index]?.face);
  return same(a.page, b.page) && same(a.handwriting, b.handwriting);
}
