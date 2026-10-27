import { type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { mergeRefs, useFocusWithin } from "react-aria";
import { Group, Input, TextField } from "react-aria-components";
import type { Insert, PageInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { ChoiceList } from "../../ui/ChoiceList";
import { Warn } from "../../ui/Warn";
import { isUndoOrRedo } from "../commands";
import { SLIP_GAP_PT } from "../constants";
import { type Editor, focusMoved } from "../editor";
import { addFaces, familyOf } from "../faces";
import { chooseFont, chosenFontOf, type FontChoice, fontChoicesOf, sameChoices, shippedNameOf } from "../fontChoices";
import { readableName } from "../fonts";
import { setWarningMuted } from "../mutedWarnings";
import {
  type Box,
  backToField,
  fontNameOf,
  fontOfNewText,
  inShippedFace,
  insertOf,
  type NewTextDraft,
  newTextFaceOf,
  newTextSpanOf,
} from "../newText";
import { shownNameIn } from "../spanFonts";
import { shallowEqual } from "../store";
import { draftNoteOf, enter, fieldFocused, finish, troublesIn, typeIntoNewText } from "../typing";
import { shortWarningOf } from "../verdicts";
import { Facts } from "./Chip";
import styles from "./Chip.module.css";
import { useEditor, useEditorState } from "./context";
import fieldStyles from "./EditField.module.css";
import { useFaceOnPage } from "./faceOnPage";
import {
  fieldScaleOf,
  newTextFieldWidthPt,
  newTextSlipLineOf,
  newTextStyleOf,
  newTextWidthIn,
  newTextWidthPt,
  points,
} from "./geometry";

/**
 * New text being typed, where it will be drawn, in the face that will draw it, and its slip under it: what it
 * comes to (Rule 1, as an edit of a span), then the fonts it can be set in. Leaving both ends the typing.
 */
export function NewTextField({ info }: { info: PageInfo }) {
  const editor = useEditor();
  const draft = useEditorState((state) => (state.draft?.kind === "newText" ? state.draft : null));
  const layout = useEditorState((state) => state.layout);
  const insert = draft === null ? null : insertOf(draft);
  // A letter the face lacks sends the line to another, put on the page only once one does.
  const face = insert === null ? null : newTextFaceOf(layout, insert);
  useFaceOnPage(face);
  const scaled = useFieldScale(draft?.style.size ?? 0, info);
  const takeFocus = useFocusSentBack(editor);
  const fieldRef = useMemo(() => mergeRefs(scaled.ref, takeFocus), [scaled.ref, takeFocus]);
  const endsOnLeaving = useEndsOnLeaving(editor);
  const placed = useRef(false);
  if (draft === null || insert === null || face === null) return null;

  // Once, as the field takes focus: what's here already is heard, and what an edit in its font warns of.
  function place() {
    if (placed.current) return;
    placed.current = true;
    fieldFocused(editor);
  }

  function onKeyDown(event: KeyboardEvent) {
    // In the slip, undo and redo are the typing's, as in a span's calendar: nothing is undone, the field's words among it.
    if (event.target !== scaled.field.current && isUndoOrRedo(event)) {
      event.preventDefault();
      return;
    }
    if (event.key !== "Escape" && !(event.key === "Enter" && event.target === scaled.field.current)) return;
    event.preventDefault();
    if (event.key === "Enter") enter(editor);
    else finish(editor, false, { returnFocus: true });
  }

  const widthPt = newTextWidthPt(insert, { face, glyphs: fontOfNewText(layout, insert)?.glyphs ?? {} });
  const fieldWidthPt = newTextFieldWidthPt(insert, widthPt);
  // Its size by CSS variables, which a touch screen's stylesheet sets it from (EditField.module.css).
  const { fontSize, height, ...where } = newTextStyleOf(insert, info, face);
  return (
    // `data-slip`: a key pressed in the slip is the slip's, as one in the field is the field's (`isTyping`).
    <Group
      {...endsOnLeaving}
      aria-label="New text and its font"
      className={fieldStyles.field}
      onKeyDown={onKeyDown}
      data-slip=""
    >
      <TextField
        aria-label={draft.of === null ? "New text" : `Change “${draft.of.edit.text}”`}
        value={draft.text}
        onChange={(next) => typeIntoNewText(editor, next, { widthOf: (typed) => newTextWidthIn(layout, typed) })}
        className={fieldStyles.field}
      >
        <Input
          ref={fieldRef}
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
            "--field-scale": scaled.scale,
          }}
        />
      </TextField>
      <NewTextChip
        draft={draft}
        info={info}
        line={newTextSlipLineOf(insert, { nearSize: draft.style.nearSize, fieldWidthPt })}
      />
    </Group>
  );
}

/** The field and what a touch screen's field scales by to show the text's size, again as the page is shown wider or narrower. */
function useFieldScale(sizePt: number, info: PageInfo) {
  const field = useRef<HTMLInputElement>(null);
  const [scale, setScale] = useState(1);
  const ref = useCallback(
    (input: HTMLInputElement | null) => {
      field.current = input;
      const layer = input?.offsetParent;
      if (input == null || !(layer instanceof HTMLElement)) return;
      const resized = new ResizeObserver(() => {
        const fontPx = parseFloat(getComputedStyle(input).fontSize);
        setScale(fieldScaleOf(sizePt, { pageWidthPt: info.width, shownPx: layer.clientWidth, fontPx }));
      });
      resized.observe(layer);
      return () => resized.disconnect();
    },
    [sizePt, info.width],
  );
  return { field, ref, scale };
}

/** A ref for the field: sent back to it from the slip, it takes focus as it's drawn again. */
function useFocusSentBack(editor: Editor) {
  const sentBack = useEditorState((state) => state.focusTo !== null && "field" in state.focusTo);
  return useCallback(
    (input: HTMLInputElement | null) => {
      if (!sentBack || input === null) return;
      input.focus();
      focusMoved(editor);
    },
    [sentBack, editor],
  );
}

/** What ends the typing: leaving the field and its slip, not a file picker taking the window's focus. */
function useEndsOnLeaving(editor: Editor) {
  const { focusWithinProps } = useFocusWithin({
    onBlurWithin: () => {
      if (document.hasFocus()) finish(editor, true);
    },
  });
  return { onFocus: focusWithinProps.onFocus, onBlur: focusWithinProps.onBlur };
}

/** The slip under new text, hung under `line`: what it comes to, the full warning as it opens, and the fonts it can be set in. */
function NewTextChip({ draft, info, line }: { draft: NewTextDraft; info: PageInfo; line: Box }) {
  const editor = useEditor();
  const kept = useKeptOnPage(line, info);
  const { trouble, note, why, shortWarning, drawnIn, shown } = useChipFacts(draft);
  const { groups, chosen, failed, widthOf } = useFontList(draft);
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
          onChoose={(key) => chooseFont(editor, key, { widthOf })}
          onEnter={() => enter(editor)}
          onPointerChoice={() => backToField(editor)}
        />
        {failed !== null && <span className={styles.facesFailed}>{failed}</span>}
      </span>
    </div>
  );
}

/** What the slip says new text being typed comes to: its troubles, its full warning, and what draws it. */
function useChipFacts(draft: NewTextDraft) {
  return useEditorState((state) => {
    const { sentences, verdict } = troublesIn(state, draft.text);
    const insert = insertOf(draft);
    const said = verdict === null ? null : draftNoteOf(state, verdict);
    const shown = shownNameIn(state.layout.fonts, newTextSpanOf(state.layout, insert));
    const named = inShippedFace(insert) ? shippedNameOf(fontNameOf(insert)) : readableName(shown);
    return {
      trouble: sentences.join("\n"),
      note: said?.sentence ?? null,
      why: said?.why ?? null,
      shortWarning: verdict === null ? null : shortWarningOf(verdict),
      drawnIn: verdict?.substitute ?? named,
      shown, // the font's name as muting knows it
    };
  }, shallowEqual);
}

/** The slip's list of fonts, each name in its own face, the one chosen, and how wide new text draws in one. */
function useFontList(draft: NewTextDraft) {
  const choices = useEditorState((state) => fontChoicesOf(state, draft), sameChoices);
  const chosen = useEditorState((state) => chosenFontOf(state.layout, draft));
  const failed = useEditorState((state) => state.shippedFaces.failed);
  const layout = useEditorState((state) => state.layout);
  // Each name is set in its own face: put on the page as the list shows.
  useEffect(() => addFaces([...choices.page, ...choices.handwriting].map((choice) => choice.face)), [choices]);
  const named = (choice: FontChoice) => ({
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
  return {
    groups,
    chosen,
    failed,
    widthOf: (insert: Insert) => newTextWidthIn(layout, insert),
  };
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
