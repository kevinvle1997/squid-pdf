import type { ReactNode } from "react";
import { Button, Group } from "react-aria-components";
import type { PageInfo } from "../../api/types";
import { putBack } from "../editor";
import { noteOf, notePlaces } from "../margin";
import type { SpanReading } from "../project";
import { useEditor } from "./context";
import { topOf } from "./geometry";
import styles from "./Margin.module.css";

interface Props {
  info: PageInfo;
  changes: readonly SpanReading[]; // the spans replaced or redacted on this page
  noteHeightPt: number; // a note's height in page points, to keep notes a hit area apart
  pageNumber: number;
}

/**
 * The user's own changes beside the page, each a button that puts the original back, level with its
 * line. On a narrow screen CSS shows the list under the page instead (`ChangeList`).
 */
export function Margin({ info, changes, noteHeightPt, pageNumber }: Props) {
  // Its grid cell stays, so a page with no changes keeps its column.
  if (changes.length === 0) return <div className={styles.margin} />;
  const places = notePlaces(changes, noteHeightPt);
  return (
    <Group className={styles.margin} aria-label={`Changes on page ${pageNumber}`}>
      {changes.map((change, index) => {
        const note = noteOf(change);
        return (
          <div key={change.span.id} className={styles.place} style={topOf(places[index] ?? 0, info)}>
            <UndoNote spanId={change.span.id} label={note.label}>
              {/* Beside the page a note is only struck words: pointed at or focused, it shows it undoes. */}
              <UndoMark />
              <span className={styles.old}>{note.struck}</span>
            </UndoNote>
          </div>
        );
      })}
    </Group>
  );
}

/** The user's own changes in a list under the page, on a narrow screen: a phone's per-change undo. */
export function ChangeList({ changes, pageNumber }: { changes: readonly SpanReading[]; pageNumber: number }) {
  if (changes.length === 0) return null;
  return (
    <section className={styles.list} aria-label={`Your changes on page ${pageNumber}`}>
      <ul>
        {changes.map((change) => {
          const note = noteOf(change);
          return (
            <li key={change.span.id}>
              <UndoNote spanId={change.span.id} label={note.label}>
                <span className={styles.old}>{change.span.text}</span>
                <span className={styles.now}>{note.now}</span>
                {/* With no Cmd+Z on a phone, each line says it's pressed to undo. */}
                <span className={styles.undo}>Undo</span>
              </UndoNote>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** One change's note: a button that puts the span's original back, named for what it undoes. */
function UndoNote({ spanId, label, children }: { spanId: string; label: string; children: ReactNode }) {
  const editor = useEditor();
  return (
    <Button className={styles.note} aria-label={label} onPress={() => putBack(editor, spanId)}>
      {children}
      <i className={styles.bar} aria-hidden="true" />
    </Button>
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
