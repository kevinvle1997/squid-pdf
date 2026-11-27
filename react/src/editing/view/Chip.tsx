import { type RefObject, useCallback } from "react";
import { FileTrigger } from "react-aria-components";
import type { PageInfo, SpanInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { Choices } from "../../ui/Choices";
import { Num } from "../../ui/Num";
import { Warn } from "../../ui/Warn";
import type { Setting } from "../fit";
import { attachFont, copyOfferedFor, FONT_FILES, readableName } from "../fonts";
import type { EditableValue } from "../formattedValues";
import { editNote, type Note } from "../marks";
import { setWarningMuted } from "../mutedWarnings";
import { redact } from "../redaction";
import { fontOf, shownNameIn } from "../spanFonts";
import { chooseWayOut, signatureWarningOf } from "../typing";
import { LOOKS_IDENTICAL, shortWarningOf, type Verdict } from "../verdicts";
import styles from "./Chip.module.css";
import { useEditor, useEditorState } from "./context";
import { keepFocusOnPress, selectedIn } from "./keys";
import { useSlipPlace } from "./slipPlace";
import { ValueEditor } from "./ValueEditor";

const SIZE = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });

interface Props {
  span: SpanInfo;
  fieldEndPt: number;
  info: PageInfo;
  trouble: string; // what won't come out as typed, a line each; empty when it fits
  verdict: Verdict; // what an edit of what's typed comes to, and in what face
  setting: Setting; // how the line is drawn, and the ways out to choose from
  value: EditableValue | undefined; // a date or an amount in what's typed, which has an editor of its own
  field: RefObject<HTMLElement | null>; // the field typed into, which the value's editor sends focus back to
  chip: RefObject<HTMLDivElement | null>; // this slip, for the field to tell focus moving into it from leaving
  // A line's: what its runs redrawn warn of, each font once, and those fonts by name, which muting mutes together.
  lineNote?: { note: Note | null; fonts: readonly string[] };
}

/** The slip under the span typed into. */
export function Chip({ span, fieldEndPt, info, trouble, verdict, setting, value, field, chip, lineNote }: Props) {
  const editor = useEditor();
  const font = useEditorState((state) => fontOf(state.layout.fonts, span));
  const sentences = useEditorState((state) => state.doc.sentences);
  const shown = useEditorState((state) => shownNameIn(state.layout.fonts, span));
  const muted = useEditorState((state) => state.mutedFonts.has(shown));
  const offered = useEditorState(copyOfferedFor);
  const attaching = useEditorState((state) => state.attaching !== null);
  const signature = useEditorState(signatureWarningOf);
  const note = lineNote === undefined ? editNote(span, { verdict, font, sentences, muted }) : lineNote.note;
  const mutes = lineNote === undefined ? [shown] : lineNote.fonts;
  const ways = setting.offered.map((id) => ({ id, label: sentences.options[id]?.label ?? id }));
  const { slipRef, style } = useSlipPlace(span.bbox, { page: info, endPt: fieldEndPt });
  const placed = useCallback(
    (element: HTMLDivElement | null) => {
      chip.current = element;
      return slipRef(element);
    },
    [chip, slipRef],
  );
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: it hears only the presses its controls don't take, to keep focus
    <div ref={placed} className={styles.chip} data-slip="" style={style} onMouseDown={keepFocusOnPress}>
      {signature !== null && (
        <span className={styles.signature} aria-hidden="true">
          <Warn mark>{signature}</Warn>
        </span>
      )}
      {note !== null && (
        <span className={styles.note}>
          <span className={styles.noteWords} aria-hidden="true">
            <Warn mark>{note.sentence}</Warn>
            {note.why !== null && <span>{note.why}</span>}
          </span>
          <Button
            variant="quiet"
            className={styles.muteWarning}
            keepFocus
            onPress={() => {
              for (const each of mutes) setWarningMuted(editor, each, { muted: true });
            }}
          >
            {mutes.length > 1 ? "Don't show again for these fonts" : "Don't show again for this font"}
          </Button>
        </span>
      )}
      <Facts
        trouble={trouble}
        shortWarning={shortWarningOf(verdict)}
        drawnIn={verdict.substitute ?? readableName(shown)}
        sizePt={span.size * setting.size}
      />
      {ways.length > 0 && (
        <span className={styles.ways}>
          <Choices
            label="How it fits"
            options={ways}
            chosen={setting.way}
            onChoose={(next) => chooseWayOut(editor, next)}
            keepFocus
          />
          <span className={styles.wayKeys} aria-hidden="true">
            <kbd>↑↓</kbd>
          </span>
        </span>
      )}
      {value !== undefined && <ValueEditor value={value} field={field} />}
      <span className={styles.actions}>
        {offered !== null && (
          // A pointer's shortcut to the Fonts list, the keyboard's way: focus stays in the field.
          <span className={styles.offer}>
            Have this font?
            <FileTrigger
              acceptedFileTypes={FONT_FILES}
              onSelect={(files) => {
                const file = files?.[0];
                if (file !== undefined) void attachFont(editor, offered, file);
              }}
            >
              <Button keepFocus isDisabled={attaching}>
                Use your copy
              </Button>
            </FileTrigger>
          </span>
        )}
        <Button keepFocus onPress={() => redact(editor, span.id, { selected: selectedIn(field.current) })}>
          Redact
        </Button>
      </span>
    </div>
  );
}

export function Facts({
  trouble,
  shortWarning,
  drawnIn,
  sizePt,
}: {
  trouble: string;
  shortWarning: string | null;
  drawnIn: string;
  sizePt: number;
}) {
  if (trouble !== "") {
    return (
      <span className={styles.bad} aria-hidden="true">
        <Warn>{trouble}</Warn>
      </span>
    );
  }
  return (
    <span className={styles.facts} aria-hidden="true">
      <span>{drawnIn}</span>
      <Num>{SIZE.format(sizePt)} pt</Num>
      {shortWarning === null ? (
        <span className={styles.identical}>{LOOKS_IDENTICAL}</span>
      ) : (
        <Warn>{shortWarning}</Warn>
      )}
    </span>
  );
}
