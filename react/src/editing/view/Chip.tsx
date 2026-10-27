// The proof slip under the field: what an edit here will come to before it's made (Rule 1), the ways
// out of a long line, and what a pointer can press there; the keyboard's ways are the field's keys and Fonts.
import { type RefObject, useCallback, useState } from "react";
import { FileTrigger } from "react-aria-components";
import type { PageInfo, SpanInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { Choices } from "../../ui/Choices";
import { Num } from "../../ui/Num";
import { Warn } from "../../ui/Warn";
import type { Setting } from "../fit";
import { attachFont, FONT_FILES, offersCopy, readableName } from "../fonts";
import type { EditableValue } from "../formattedValues";
import { editNote } from "../marks";
import { setWarningMuted } from "../mutedWarnings";
import { redact } from "../redaction";
import { fontOf, shownNameIn } from "../spanFonts";
import { chooseWayOut, signatureWarningOf } from "../typing";
import { LOOKS_IDENTICAL, shortWarningOf, type Verdict } from "../verdicts";
import styles from "./Chip.module.css";
import { useEditor, useEditorState } from "./context";
import { pinnedUnder, slipShiftPx } from "./geometry";
import { ValueEditor } from "./ValueEditor";

const SIZE = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });

interface Props {
  span: SpanInfo;
  info: PageInfo;
  trouble: string; // what won't come out as typed, a line each; empty when it fits
  verdict: Verdict; // what an edit of what's typed comes to, and in what face
  setting: Setting; // how the line is drawn, and the ways out to choose from
  value: EditableValue | undefined; // a date or an amount in what's typed, which has an editor of its own
  field: RefObject<HTMLInputElement | null>; // the field typed into, which the value's editor sends focus back to
  slip: RefObject<HTMLDivElement | null>; // this slip, for the field to tell focus moving into it from leaving
}

/** The slip under the span typed into. */
export function Chip({ span, info, trouble, verdict, setting, value, field, slip }: Props) {
  const editor = useEditor();
  const font = useEditorState((state) => fontOf(state.layout.fonts, span));
  const sentences = useEditorState((state) => state.doc.sentences);
  // What draws the edit: the span's similar font, or else its own, which the preview can only stand in for.
  const shown = useEditorState((state) => shownNameIn(state.layout.fonts, span));
  // What an edit here warns of, in full, as typing starts: until the user mutes its font's warning.
  const muted = useEditorState((state) => state.mutedFonts.has(shown));
  const offered = useEditorState(offersCopy);
  const attaching = useEditorState((state) => state.attaching !== null);
  // A signed document's, until its first edit: no mute, since that edit ends it.
  const signature = useEditorState(signatureWarningOf);
  const note = editNote(span, { verdict, font, sentences, muted });
  const ways = setting.offered.map((id) => ({ id, label: sentences.options[id]?.label ?? id }));
  // Kept on the page's width as what it holds grows, or the page is shown another size: an open calendar is the widest.
  const [shiftPx, setShiftPx] = useState(0);
  const keepOnPage = useCallback(
    (chip: HTMLDivElement | null) => {
      slip.current = chip;
      const layer = chip?.offsetParent;
      if (chip == null || !(layer instanceof HTMLElement)) return;
      const resized = new ResizeObserver(() => {
        setShiftPx(slipShiftPx(span.bbox, info, { slipWidthPx: chip.offsetWidth, pageWidthPx: layer.clientWidth }));
      });
      resized.observe(chip);
      resized.observe(layer);
      return () => resized.disconnect();
    },
    [slip, span.bbox, info],
  );
  return (
    <div
      ref={keepOnPage}
      className={styles.chip}
      style={{ ...pinnedUnder(span.bbox, info), translate: shiftPx === 0 ? undefined : `${shiftPx}px 0` }}
    >
      {signature !== null && (
        // Announced as the field takes focus, before the font's note.
        <span className={styles.signature} aria-hidden="true">
          <Warn mark>{signature}</Warn>
        </span>
      )}
      {note !== null && (
        // Announced as the field takes focus; its button is a pointer's, the keyboard's way is the Fonts list.
        <span className={styles.note}>
          <span className={styles.noteWords} aria-hidden="true">
            <Warn mark>{note.sentence}</Warn>
            {note.why !== null && <span>{note.why}</span>}
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
      <Facts
        trouble={trouble}
        shortWarning={shortWarningOf(verdict)}
        drawnIn={verdict.substitute ?? readableName(shown)}
        sizePt={span.size * setting.size}
      />
      {ways.length > 0 && (
        // A pointer's way to choose; the keyboard's is Up and Down in the field, which keeps focus.
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
        {offered && (
          // A pointer's shortcut to the Fonts list, the keyboard's way: focus stays in the field.
          <span className={styles.offer}>
            Have this font?
            <FileTrigger
              acceptedFileTypes={FONT_FILES}
              onSelect={(files) => {
                const file = files?.[0];
                if (file !== undefined) void attachFont(editor, span.font, file);
              }}
            >
              <Button keepFocus isDisabled={attaching}>
                Use your copy
              </Button>
            </FileTrigger>
          </span>
        )}
        {/* Redaction where a pointer or a finger can reach it: a key on the mark is the keyboard's way. */}
        <span className={styles.redact}>
          <Button keepFocus onPress={() => redact(editor, span.id)}>
            Redact
          </Button>
        </span>
      </span>
    </div>
  );
}

/** The edit's facts before it's made: what won't come out as typed, else what draws it and whether it will match. */
function Facts({
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
      {/* Find's own words when it will match. */}
      {shortWarning === null ? (
        <span className={styles.identical}>{LOOKS_IDENTICAL}</span>
      ) : (
        <Warn>{shortWarning}</Warn>
      )}
    </span>
  );
}
