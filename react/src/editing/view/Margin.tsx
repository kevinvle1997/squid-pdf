import type { ReactNode } from "react";
import { Button, Group } from "react-aria-components";
import type { PageInfo } from "../../api/types";
import { Tip } from "../../ui/Tooltip";
import { type Editor, putBack } from "../editor";
import { type NoteWords, newTextNoteOf, noteOf, notePlaces } from "../margin";
import { takeOutNewText } from "../newText";
import type { NewTextReading, SpanReading } from "../project";
import { sentenceOrNothing } from "../words";
import { useEditor, useEditorState } from "./context";
import { newTextBoxOf, topOf } from "./geometry";
import styles from "./Margin.module.css";

interface Props {
  info: PageInfo;
  changes: readonly SpanReading[]; // the spans replaced or redacted on this page
  texts: readonly NewTextReading[]; // the new text on it
  noteHeightPt: number; // a note's height in page points, to keep notes a hit area apart
  pageNumber: number;
}

/** One note in the margin: what it says, the line it's about, and what pressing it undoes. */
interface MarginNote {
  readonly key: string;
  readonly note: NoteWords;
  readonly line: { y0: number; y1: number };
  readonly undo: (editor: Editor) => void;
}

/** The page's notes: each changed span's, then each piece of new text's, which a press takes out. */
function notesOf(changes: readonly SpanReading[], texts: readonly NewTextReading[]): MarginNote[] {
  const spans = changes.map((change) => ({
    key: change.span.id,
    note: noteOf(change),
    line: change.span.bbox,
    undo: (editor: Editor) => putBack(editor, change.span.id),
  }));
  const added = texts.map((text) => ({
    key: `text ${text.newTextId}`,
    note: newTextNoteOf(text),
    line: newTextBoxOf(text.edit, 0),
    undo: (editor: Editor) => takeOutNewText(editor, text.newTextId),
  }));
  return [...spans, ...added];
}

/**
 * The user's own changes beside the page, each a button that puts the original back, or takes new text out,
 * level with its line. On a narrow screen CSS shows the list under the page instead (`ChangeList`).
 */
export function Margin({ info, changes, texts, noteHeightPt, pageNumber }: Props) {
  // Its grid cell stays, so a page with no changes keeps its column.
  const newText = useEditorState((state) => sentenceOrNothing(state.doc.sentences.new_text));
  if (changes.length === 0 && texts.length === 0) return <div className={styles.margin} />;
  const notes = notesOf(changes, texts);
  const places = notePlaces(
    notes.map(({ line }) => line),
    noteHeightPt,
  );
  return (
    <Group className={styles.margin} aria-label={`Changes on page ${pageNumber}`}>
      {notes.map(({ key, note, undo }, index) => (
        <div key={key} className={styles.place} style={topOf(places[index] ?? 0, info)}>
          <Tip heading={note.was === "" ? newText : `Was “${note.was}”`} detail={`Now “${note.now}”`}>
            <UndoNote label={note.label} undo={undo}>
              {/* Beside the page a note is only the words changed: pointed at or focused, it shows it undoes. */}
              <UndoMark />
              {note.inserted === null ? (
                <span className={styles.old}>{note.struck}</span>
              ) : (
                // Words only added: they're marked as a proof marks an insertion, not one kept struck.
                <span className={styles.added}>
                  <InsertMark />
                  {note.inserted}
                </span>
              )}
            </UndoNote>
          </Tip>
        </div>
      ))}
    </Group>
  );
}

/** The user's own changes in a list under the page, on a narrow screen: a phone's per-change undo. */
export function ChangeList({
  changes,
  texts,
  pageNumber,
}: {
  changes: readonly SpanReading[];
  texts: readonly NewTextReading[];
  pageNumber: number;
}) {
  const newText = useEditorState((state) => sentenceOrNothing(state.doc.sentences.new_text));
  if (changes.length === 0 && texts.length === 0) return null;
  return (
    <section className={styles.list} aria-label={`Your changes on page ${pageNumber}`}>
      <ul>
        {notesOf(changes, texts).map(({ key, note, undo }) => (
          <li key={key}>
            <UndoNote label={note.label} undo={undo}>
              {note.was === "" ? (
                <span className={styles.added}>
                  <InsertMark />
                  {newText}
                </span>
              ) : (
                <span className={styles.old}>{note.was}</span>
              )}
              <span className={styles.now}>{note.now}</span>
              {/* With no Cmd+Z on a phone, each line says it's pressed to undo. */}
              <span className={styles.undo}>Undo</span>
            </UndoNote>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** One change's note: a button that undoes it, named for what it undoes. */
function UndoNote({ label, undo, children }: { label: string; undo: (editor: Editor) => void; children: ReactNode }) {
  const editor = useEditor();
  return (
    <Button className={styles.note} aria-label={label} onPress={() => undo(editor)}>
      {children}
      <i className={styles.bar} aria-hidden="true" />
    </Button>
  );
}

/** An insertion, drawn as a proofreader's caret, at the words' own size. The note's name says what it undoes. */
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

/** Undo, drawn: an arrow curling back, at its own size so the stroke stays 1.5px. The note's name says it. */
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
