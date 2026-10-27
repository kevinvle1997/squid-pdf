import { Mascot } from "../../ui/Mascot";
import { sentenceOrNothing } from "../words";
import { useEditorState } from "./context";
import styles from "./HowToStart.module.css";

/**
 * How to start, until the first edit: the squid in the first page's margin says how to fix a word and how to add
 * text. Where there's no margin, on a narrow screen, its words sit above the first page instead (`beside` false).
 * A reading persisted before its sentences were sent shows none of it.
 */
export function HowToStart({ beside }: { beside: boolean }) {
  const sentences = useEditorState((state) => state.doc.sentences);
  // The page list's ticks hang beside the first page too: the note goes under them.
  const ticks = useEditorState((state) => state.reading.order.length);
  const lines = [sentenceOrNothing(sentences.how_to_fix), sentenceOrNothing(sentences.how_to_add)];
  if (lines.every((line) => line === "")) return null;
  return (
    <div className={beside ? styles.beside : styles.above} style={{ "--ticks": ticks }}>
      <p className={styles.words}>{lines.map((line) => line !== "" && <span key={line}>{line}</span>)}</p>
      {beside && <Mascot className={styles.squid} />}
    </div>
  );
}
