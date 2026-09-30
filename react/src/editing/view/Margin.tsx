import { Button } from "react-aria-components";
import type { PageInfo } from "../../api/types";
import { putBack } from "../editor";
import { notePlaces } from "../margin";
import type { SpanReading } from "../project";
import { useEditor } from "./context";
import styles from "./Margin.module.css";

interface Props {
  info: PageInfo;
  changes: readonly SpanReading[]; // the spans replaced on this page
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

  const notes = changes.map(({ span, text: now }) => (
    <Button
      key={span.id}
      className={styles.note}
      aria-label={`Undo: “${now}” goes back to “${span.text}”`}
      onPress={() => putBack(editor, span.id)}
    >
      <span className={styles.old}>{span.text}</span>
      {shape === "list" && <span className={styles.now}>{now}</span>}
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
