import { Button } from "react-aria-components";
import type { PageInfo, SpanInfo } from "../api/types";
import { useEditor } from "./context";
import styles from "./Margin.module.css";

interface Props {
  info: PageInfo;
  spans: SpanInfo[]; // the spans changed on this page
  pageHeight: number; // in CSS pixels, to keep notes a hit area apart
  shape: "margin" | "list";
  label: string;
}

/** A note's height in CSS pixels: the hit area --hit sets, larger on a touch screen. */
function noteHeight(): number {
  return parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--hit"));
}

/**
 * The user's own changes, each a button that puts the original back. Beside the page on
 * a wide screen, in a list under it on a narrow one; CSS shows one or the other.
 */
export function Margin({ info, spans, pageHeight, shape, label }: Props) {
  const editor = useEditor();
  if (spans.length === 0) return shape === "margin" ? <div className={styles.margin} /> : null;

  const notes = spans.map((span) => {
    const now = editor.latest.get(span.id) ?? span.text;
    return (
      <Button
        key={span.id}
        className={styles.note}
        aria-label={`Undo: “${now}” goes back to “${span.text}”`}
        onPress={() => editor.revert(span.id)}
      >
        <span className={styles.old}>{span.text}</span>
        {shape === "list" && <span className={styles.now}>{now}</span>}
        <i className={styles.bar} aria-hidden="true" />
      </Button>
    );
  });

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

  // Each note level with its span, nudged down so no two overlap.
  const gap = pageHeight > 0 ? ((noteHeight() || 0) / pageHeight) * info.height : 0;
  const order = spans
    .map((span, index) => ({ index, want: (span.bbox.y0 + span.bbox.y1) / 2 }))
    .sort((a, b) => a.want - b.want);
  const tops = new Map<number, number>();
  let last = -Infinity;
  for (const { index, want } of order) {
    last = Math.max(want, last + gap);
    tops.set(index, last);
  }
  return (
    <div className={styles.margin} aria-label={`Changes on ${label.toLowerCase()}`} role="group">
      {notes.map((note, index) => (
        <div key={note.key} className={styles.place} style={{ top: `${((tops.get(index) ?? 0) / info.height) * 100}%` }}>
          {note}
        </div>
      ))}
    </div>
  );
}
