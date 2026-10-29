import styles from "./ScreenTop.module.css";
import { Wordmark } from "./Wordmark";

/** The top of a screen with no document on it: the wordmark, where the editor's bar puts it; with `home`, a link to the landing. */
export function ScreenTop({ home = false }: { home?: boolean }) {
  return (
    <header className={styles.top}>
      {home ? (
        <a className={styles.home} href="/">
          <Wordmark />
        </a>
      ) : (
        <Wordmark />
      )}
    </header>
  );
}
