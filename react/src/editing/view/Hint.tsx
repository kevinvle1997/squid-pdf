import { changedCount } from "../editor";
import { useEditorState } from "./context";
import styles from "./Hint.module.css";

/**
 * How to start, until something's changed: one quiet line, in the first page's margin where the changes
 * will be noted, or over the page where a narrow screen has no margin. The landing said it once; a
 * first-timer who scrolled past it, or a touch screen with nothing to hover, needs it where the words are.
 */
export function Hint({ where }: { where: "margin" | "over" }) {
  const unchanged = useEditorState((state) => changedCount(state) === 0);
  if (!unchanged) return null;
  return (
    <p className={where === "margin" ? styles.inMargin : styles.over}>
      <span className={styles.click}>Click any word to fix it</span>
      <span className={styles.tap}>Tap any word to fix it</span>
    </p>
  );
}
