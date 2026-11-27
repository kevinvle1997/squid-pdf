import { Fragment, type ReactNode } from "react";
import { Button, Group } from "react-aria-components";
import type { PageInfo } from "../../api/types";
import { Tip } from "../../ui/Tooltip";
import type { Editor, Layout } from "../editor";
import { type Cut, inLine, joinedRuns, marginNotesOf, notePlaces, UNCUT } from "../margin";
import type { NewTextReading, SpanReading } from "../project";
import { sentenceOrNothing } from "../words";
import { useEditor, useEditorState } from "./context";
import { topOf } from "./geometry";
import styles from "./Margin.module.css";

interface Props {
  info: PageInfo;
  changes: readonly SpanReading[];
  newTexts: readonly NewTextReading[];
  lines: Layout["lines"];
  noteHeightPt: number;
  pageNumber: number;
}

export function Margin({ info, changes, newTexts, lines, noteHeightPt, pageNumber }: Props) {
  const newText = useEditorState((state) => sentenceOrNothing(state.doc.sentences.new_text));
  if (changes.length === 0 && newTexts.length === 0) return <div className={styles.margin} />;
  const notes = marginNotesOf(changes, newTexts, lines);
  const places = notePlaces(
    notes.map(({ line }) => line),
    noteHeightPt,
  );
  return (
    <Group className={styles.margin} aria-label={`Changes on page ${pageNumber}`}>
      {notes.map(({ key, note, undo }, index) => (
        <div key={key} className={styles.place} style={topOf(places[index] ?? 0, info)}>
          <Tip
            heading={note.was.length === 0 ? newText : `Was “${inLine(joinedRuns(note.was), note.cut)}”`}
            detail={`Now “${inLine(note.now, note.cut)}”`}
            changed={note.was.length > 0}
          >
            <UndoNote label={note.label} undo={undo}>
              <UndoMark />
              {note.inserted === null ? (
                <Struck runs={note.struck} />
              ) : (
                <span className={styles.added}>
                  <InsertMark />
                  <span className={styles.addedWords}>{note.inserted}</span>
                </span>
              )}
            </UndoNote>
          </Tip>
        </div>
      ))}
    </Group>
  );
}

function Struck({ runs, cut = UNCUT }: { runs: readonly string[]; cut?: Cut }) {
  return (
    <span className={styles.old}>
      {cut.before && <span className={styles.between}>… </span>}
      {runs.map((run, index) => (
        <Fragment key={index}>
          {index > 0 && <span className={styles.between}> … </span>}
          <span className={styles.run}>{run}</span>
        </Fragment>
      ))}
      {cut.after && <span className={styles.between}> …</span>}
    </span>
  );
}

export function ChangeList({
  changes,
  newTexts,
  lines,
  pageNumber,
}: {
  changes: readonly SpanReading[];
  newTexts: readonly NewTextReading[];
  lines: Layout["lines"];
  pageNumber: number;
}) {
  const newText = useEditorState((state) => sentenceOrNothing(state.doc.sentences.new_text));
  if (changes.length === 0 && newTexts.length === 0) return null;
  return (
    <section className={styles.list} aria-label={`Your changes on page ${pageNumber}`}>
      <ul>
        {marginNotesOf(changes, newTexts, lines).map(({ key, note, undo }) => (
          <li key={key}>
            <UndoNote label={note.label} undo={undo}>
              {note.was.length === 0 ? (
                <span className={styles.added}>
                  <InsertMark />
                  {newText}
                </span>
              ) : (
                <Struck runs={note.was} cut={note.cut} />
              )}
              <span className={styles.now}>{inLine(note.now, note.cut)}</span>
              <span className={styles.undo}>Undo</span>
            </UndoNote>
          </li>
        ))}
      </ul>
    </section>
  );
}

function UndoNote({ label, undo, children }: { label: string; undo: (editor: Editor) => void; children: ReactNode }) {
  const editor = useEditor();
  return (
    <Button className={styles.note} aria-label={label} onPress={() => undo(editor)} data-margin-note="">
      {children}
      <i className={styles.bar} aria-hidden="true" />
    </Button>
  );
}

function InsertMark() {
  return (
    <svg
      width="8"
      height="10"
      viewBox="0 0 8 10"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={styles.caret}
    >
      <path d="M1 8.5 4 2.5l3 6" />
    </svg>
  );
}

function UndoMark() {
  return (
    <span className={styles.undoMark}>
      <svg
        width="12"
        height="12"
        viewBox="0 0 12 12"
        aria-hidden="true"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M5 2 2.5 4.5 5 7M2.5 4.5h4.75a2.75 2.75 0 0 1 0 5.5H5" />
      </svg>
    </span>
  );
}
