// The proof slip under the field: what an edit here will come to before it's made (Rule 1), the ways
// out of a long line, and what a pointer can press there; the keyboard's ways are the field's keys and Fonts.
import { FileTrigger } from "react-aria-components";
import type { PageInfo, SpanInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { Choices } from "../../ui/Choices";
import { Num } from "../../ui/Num";
import { Warn } from "../../ui/Warn";
import type { Setting } from "../fit";
import { attachFont, FONT_FILES, offersCopy, shownNameOf } from "../fonts";
import { editNote, shortWarningOf } from "../marks";
import { quietFont } from "../quiet";
import { redact } from "../redaction";
import { chooseWayOut } from "../typing";
import styles from "./Chip.module.css";
import { useEditor, useEditorState } from "./context";
import { pinnedUnder } from "./geometry";

const SIZE = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });

interface Props {
  span: SpanInfo;
  info: PageInfo;
  trouble: string; // what won't come out as typed, a line each; empty when it fits
  setting: Setting; // how the line is drawn, and the ways out to choose from
}

/** The slip under the span typed into. */
export function Chip({ span, info, trouble, setting }: Props) {
  const editor = useEditor();
  const font = useEditorState((state) => state.layout.fonts.get(span.font));
  const copy = useEditorState((state) => state.doc.copy);
  // What draws the edit: the span's similar font, or else its own, which the preview can only stand in for.
  const shown = useEditorState((state) => shownNameOf(state.layout, span.font));
  // What an edit here warns of, in full, as typing starts: until the user quiets its font.
  const quiet = useEditorState((state) => state.quietFonts.has(shown));
  const offered = useEditorState(offersCopy);
  const attaching = useEditorState((state) => state.attaching !== null);
  const note = editNote(span, { font, copy, quiet });
  const ways = setting.offered.map((id) => ({ id, label: copy.options[id]?.label ?? id }));
  return (
    <div className={styles.chip} style={pinnedUnder(span.bbox, info)}>
      {note !== null && (
        // Said as the field takes focus; its button is a pointer's, the keyboard's way is the Fonts list.
        <span className={styles.note}>
          <span className={styles.noteWords} aria-hidden="true">
            <Warn mark>{note.said}</Warn>
            {note.why !== null && <span>{note.why}</span>}
          </span>
          <Button preventFocusOnPress excludeFromTabOrder onPress={() => quietFont(editor, shown, { quiet: true })}>
            Don't show again for this font
          </Button>
        </span>
      )}
      <Verdict span={span} trouble={trouble} drawnIn={span.substitute ?? shown} sizePt={span.size * setting.size} />
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
              <Button preventFocusOnPress excludeFromTabOrder isDisabled={attaching}>
                Use your copy
              </Button>
            </FileTrigger>
          </span>
        )}
        {/* Redaction where a pointer or a finger can reach it: a key on the mark is the keyboard's way. */}
        <span className={styles.redact}>
          <Button preventFocusOnPress excludeFromTabOrder onPress={() => redact(editor, span.id)}>
            Redact
          </Button>
        </span>
      </span>
    </div>
  );
}

/** The edit's verdict before it's made: what won't come out as typed, else what draws it and whether it will match. */
function Verdict({
  span,
  trouble,
  drawnIn,
  sizePt,
}: {
  span: SpanInfo;
  trouble: string;
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
  const shortWarning = shortWarningOf(span);
  return (
    <span className={styles.facts} aria-hidden="true">
      <span>{drawnIn}</span>
      <Num>{SIZE.format(sizePt)} pt</Num>
      {/* Find's own words when it will match. */}
      {shortWarning === null ? <span className={styles.identical}>looks identical</span> : <Warn>{shortWarning}</Warn>}
    </span>
  );
}
