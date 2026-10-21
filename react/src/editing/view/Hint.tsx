import { changedCount } from "../editor";
import { useEditorState } from "./context";
import styles from "./Hint.module.css";

/** How to start, said by the squid in the first page's margin until something's changed and noted there. */
export function Hint() {
  const unchanged = useEditorState((state) => changedCount(state) === 0);
  if (!unchanged) return null;
  return (
    <div className={styles.hint}>
      <p className={styles.says}>
        <HintWords />
      </p>
      <span className={styles.squid} />
    </div>
  );
}

/** How to start, in words: a click with a pointer, a double-tap on a touch screen. */
export function HintWords() {
  return (
    <>
      <span className={styles.click}>Click any word to fix it</span>
      <span className={styles.tap}>Double-tap any word to fix it</span>
    </>
  );
}
