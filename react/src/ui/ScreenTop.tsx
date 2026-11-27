import styles from "./ScreenTop.module.css";
import { Wordmark } from "./Wordmark";

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
