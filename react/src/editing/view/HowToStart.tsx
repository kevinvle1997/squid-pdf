import { useSyncExternalStore } from "react";
import { coarsePointer, onPointerChange } from "../../ui/pointer";
import { sentenceOrNothing } from "../words";
import { useEditorState } from "./context";
import styles from "./HowToStart.module.css";

export function HowToStart({ beside }: { beside: boolean }) {
  const sentences = useEditorState((state) => state.doc.sentences);
  const ticks = useEditorState((state) => state.reading.order.length);
  const touch = useSyncExternalStore(onPointerChange, coarsePointer);
  const lines = [
    sentenceOrNothing(touch ? sentences.how_to_fix_touch : sentences.how_to_fix),
    sentenceOrNothing(touch ? sentences.how_to_add_touch : sentences.how_to_add),
  ];
  if (lines.every((line) => line === "")) return null;
  return (
    <div className={beside ? styles.beside : styles.above} style={{ "--ticks": ticks }}>
      <p className={styles.words}>{lines.map((line) => line !== "" && <span key={line}>{line}</span>)}</p>
    </div>
  );
}
