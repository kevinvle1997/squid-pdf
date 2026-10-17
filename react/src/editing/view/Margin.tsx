import { Button } from "react-aria-components";
import type { PageInfo } from "../../api/types";
import { putBack } from "../editor";
import { changedWordsIn, notePlaces } from "../margin";
import type { SpanReading } from "../project";
import { useEditor } from "./context";
import styles from "./Margin.module.css";

interface Props {
  info: PageInfo;
  changes: readonly SpanReading[]; // the spans replaced or redacted on this page
  gapPt: number; // a note's height in page points, to keep notes a hit area apart
  shape: "margin" | "list";
  label: string;
}

/**
 * The user's own changes, each a button that puts the original back. Beside the page on
 * a wide screen, in a list under it on a narrow one; CSS shows one or the other.
 */
export function Margin({ info, changes, gapPt, shape, label }: Props) {
  const editor = useEditor();
  if (changes.length === 0) return shape === "margin" ? <div className={styles.margin} /> : null;

  const notes = changes.map(({ span, text: now, redacted }) => (
    <Button
      key={span.id}
      className={styles.note}
      aria-label={
        redacted ? `Undo: “${span.text}” is no longer redacted` : `Undo: “${now}” goes back to “${span.text}”`
      }
      onPress={() => putBack(editor, span.id)}
    >
      {/* Beside the page a note is only struck words: pointed at or focused, it shows it undoes. */}
      {shape === "margin" && <UndoMark />}
      {/* A redaction takes every word out, so the note strikes them all. */}
      <span className={styles.old}>{shape === "margin" && !redacted ? changedWordsIn(span.text, now) : span.text}</span>
      {shape === "list" && <span className={styles.now}>{redacted ? "Redacted" : now}</span>}
      {/* The list is a phone's per-change undo, with no Cmd+Z: each line says it's pressed to undo. */}
      {shape === "list" && <span className={styles.undo}>Undo</span>}
      <i className={styles.bar} aria-hidden="true" />
    </Button>
  ));

  if (shape === "list") {
    return (
      <section className={styles.list} aria-label={`Your changes on ${label.toLowerCase()}`}>
        <ul>
          {notes.map((note) => (
            <li key={note.key}>{note}</li>
          ))}
        </ul>
      </section>
    );
  }

  const places = notePlaces(changes, gapPt);
  return (
    // biome-ignore lint/a11y/useSemanticElements: a fieldset is for a form's inputs; these are notes on a proof.
    <div className={styles.margin} aria-label={`Changes on ${label.toLowerCase()}`} role="group">
      {notes.map((note, index) => (
        <div key={note.key} className={styles.place} style={{ top: `${((places[index] ?? 0) / info.height) * 100}%` }}>
          {note}
        </div>
      ))}
    </div>
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
