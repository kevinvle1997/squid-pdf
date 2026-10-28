import {
  type FocusEvent,
  type KeyboardEvent,
  type RefObject,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { mergeProps, mergeRefs, useFocusVisible, useFocusWithin } from "react-aria";
import { Input, TextField } from "react-aria-components";
import { createPortal } from "react-dom";
import type { Insert, PageInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { ChoiceList } from "../../ui/ChoiceList";
import { Warn } from "../../ui/Warn";
import { isUndoOrRedo } from "../commands";
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
  pastFaces,
  toFaces,
} from "../newText";
import { shownNameIn } from "../spanFonts";
import { shallowEqual } from "../store";
import { draftNoteOf, enter, fieldFocused, finish, troublesIn, typeIntoNewText } from "../typing";
import { shortWarningOf } from "../verdicts";
import { Facts } from "./Chip";
import styles from "./Chip.module.css";
import { SlipLayerContext, useEditor, useEditorState } from "./context";
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
import { keepFocusOnPress } from "./keys";
import { useSlipPlace } from "./slipPlace";

/**
 * New text being typed, where it will be drawn, in the face that will draw it, and its slip: what it comes to (Rule 1,
 * as an edit of a span), then the fonts it can be set in. Leaving both ends the typing.
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
  const takeFocus = useFocusSent(editor, "field");
  const fieldRef = useMemo(() => mergeRefs<HTMLInputElement>(scaled.ref, takeFocus), [scaled.ref, takeFocus]);
  const slip = useRef<HTMLDivElement>(null);
  // The page's layer for slips, which its sheet doesn't clip: a slip near the foot hangs past it, over the next page.
  const slipLayer = useContext(SlipLayerContext);
  const placed = useRef(false);
  const { isFocusVisible } = useFocusVisible();
  if (draft === null || insert === null || face === null) return null;

  // Once, as the field takes focus: what's here already is heard, and what an edit in its font warns of.
  function place() {
    if (placed.current) return;
    placed.current = true;
    fieldFocused(editor);
  }

  // Leaving the field and its slip ends the typing, but not a file picker taking the window's focus.
  function leaving(event: FocusEvent) {
    const to = event.relatedTarget;
    if (to instanceof Node && (scaled.field.current?.contains(to) || slip.current?.contains(to))) return;
    // Tabbed past all there is, focus would fall to the document: it goes to the new text, as Enter sends it.
    const tabbedOut = to === null && isFocusVisible;
    if (document.hasFocus()) finish(editor, true, { returnFocus: tabbedOut });
  }

  function onKeyDown(event: KeyboardEvent) {
    // On to the fonts, as Tab would reach them were the slip not apart from the page.
    if (event.key === "Tab" && !event.shiftKey) {
      event.preventDefault();
      toFaces(editor);
      return;
    }
    if (event.key !== "Enter" && event.key !== "Escape") return;
    event.preventDefault();
    if (event.key === "Enter") enter(editor);
    else finish(editor, false, { returnFocus: true });
  }

  const widthPt = newTextWidthPt(insert, { face, glyphs: fontOfNewText(layout, insert)?.glyphs ?? {} });
  const fieldWidthPt = newTextFieldWidthPt(insert, widthPt);
  // Its size by CSS variables, which a touch screen's stylesheet sets it from (EditField.module.css).
  const { fontSize, height, ...where } = newTextStyleOf(insert, info, face);
  return (
    <>
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
          onBlur={leaving}
          onKeyDown={onKeyDown}
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
      {slipLayer !== null &&
        createPortal(
          <NewTextChip
            draft={draft}
            info={info}
            line={newTextSlipLineOf(insert, { nearSize: draft.style.nearSize, fieldWidthPt })}
            slip={slip}
            onLeave={leaving}
          />,
          slipLayer,
        )}
    </>
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

/** A ref for the part of new text being typed that `part` names: focus sent there, it takes it as it's drawn again. */
function useFocusSent(editor: Editor, part: "field" | "faces") {
  const sent = useEditorState((state) => state.focusTo !== null && part in state.focusTo);
  return useCallback(
    (element: HTMLElement | null) => {
      if (!sent || element === null) return;
      element.focus();
      focusMoved(editor);
    },
    [sent, editor],
  );
}

/**
 * The slip of new text, hung from `line` on the page's layer for slips, as a span's chip is: what it comes to, the full
 * warning as it opens, and the fonts it can be set in. Focus leaving it goes through `onLeave`, as the field's does.
 */
function NewTextChip({
  draft,
  info,
  line,
  slip,
  onLeave,
}: {
  draft: NewTextDraft;
  info: PageInfo;
  line: Box;
  slip: RefObject<HTMLDivElement | null>;
  onLeave: (event: FocusEvent) => void;
}) {
  const editor = useEditor();
  const { trouble, note, why, shortWarning, drawnIn, shown } = useChipFacts(draft);
  const { groups, chosen, failed, widthOf } = useFontList(draft);
  // On the side of its line it was on while the fonts are in use: a face chosen grows or shrinks it, under the keys.
  const [inUse, setInUse] = useState(false);
  const { focusWithinProps } = useFocusWithin({ onFocusWithinChange: setInUse });
  const { slipRef, style } = useSlipPlace(line, { page: info, holdSide: inUse });
  const placed = useCallback(
    (element: HTMLDivElement | null) => {
      slip.current = element;
      return slipRef(element);
    },
    [slip, slipRef],
  );
  const takeFocus = useFocusSent(editor, "faces");

  function onKeyDown(event: KeyboardEvent) {
    // Undo and redo are the typing's, as in a span's calendar: nothing is undone, the field's words among it.
    if (isUndoOrRedo(event)) {
      event.preventDefault();
      return;
    }
    // Back to the field, which the slip hangs apart from, or on to what follows the field on its page.
    if (event.key === "Tab") {
      if (event.shiftKey) backToField(editor);
      else if (!pastFaces(editor)) return;
      event.preventDefault();
      return;
    }
    if (event.key !== "Escape") return;
    event.preventDefault();
    finish(editor, false, { returnFocus: true });
  }

  return (
    // `data-slip`: a key pressed in it is the slip's, as one in the field is the field's (`isTyping`).
    <div
      ref={placed}
      className={styles.chip}
      style={style}
      data-slip=""
      {...mergeProps(focusWithinProps, { onBlur: onLeave, onKeyDown, onMouseDown: keepFocusOnPress })}
    >
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
          listRef={takeFocus}
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
