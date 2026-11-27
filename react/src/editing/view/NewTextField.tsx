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
import { mergeRefs } from "react-aria";
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
import { useLeaveTyping } from "./leaveTyping";
import { useSlipPlace } from "./slipPlace";

export function NewTextField({ info }: { info: PageInfo }) {
  const editor = useEditor();
  const draft = useEditorState((state) => (state.draft?.kind === "newText" ? state.draft : null));
  const layout = useEditorState((state) => state.layout);
  const insert = draft === null ? null : insertOf(draft);
  const face = insert === null ? null : newTextFaceOf(layout, insert);
  useFaceOnPage(face);
  const scaled = useFieldScale(draft?.style.size ?? 0, info);
  const takeFocus = useFocusSent(editor, "field");
  const fieldRef = useMemo(() => mergeRefs<HTMLInputElement>(scaled.ref, takeFocus), [scaled.ref, takeFocus]);
  const slip = useRef<HTMLDivElement>(null);
  const slipLayer = useContext(SlipLayerContext);
  const placed = useRef(false);
  const leave = useLeaveTyping();
  if (draft === null || insert === null || face === null) return null;

  function place() {
    if (placed.current) return;
    placed.current = true;
    fieldFocused(editor);
  }

  function leaving(event: FocusEvent) {
    const to = event.relatedTarget;
    if (to instanceof Node && (scaled.field.current?.contains(to) || slip.current?.contains(to))) return;
    leave(event);
  }

  function onKeyDown(event: KeyboardEvent) {
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
  const { slipRef, style } = useSlipPlace(line, { page: info });
  const placed = useCallback(
    (element: HTMLDivElement | null) => {
      slip.current = element;
      return slipRef(element);
    },
    [slip, slipRef],
  );
  const takeFocus = useFocusSent(editor, "faces");

  function onKeyDown(event: KeyboardEvent) {
    if (isUndoOrRedo(event)) {
      event.preventDefault();
      return;
    }
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
    // biome-ignore lint/a11y/noStaticElementInteractions: it hears the keys and presses its controls leave, and focus leaving it
    <div
      ref={placed}
      className={styles.chip}
      style={style}
      data-slip=""
      onBlur={onLeave}
      onKeyDown={onKeyDown}
      onMouseDown={keepFocusOnPress}
    >
      {note !== null && (
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
      shown,
    };
  }, shallowEqual);
}

function useFontList(draft: NewTextDraft) {
  const choices = useEditorState((state) => fontChoicesOf(state, draft), sameChoices);
  const chosen = useEditorState((state) => chosenFontOf(state.layout, draft));
  const failed = useEditorState((state) => state.shippedFaces.failed);
  const layout = useEditorState((state) => state.layout);
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
