import { classes } from "./classes";
import styles from "./Wordmark.module.css";

/** The mascot's outline, drawn for 18 px, and the name; `compact`, a narrow screen shows the squid alone, the name still read. */
export function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <span className={classes(styles.wordmark, compact && styles.compact)}>
      <svg viewBox="0 0 18 18" aria-hidden="true">
        <path
          d="M9 1.3c2.3 1.7 4 4 4 6.6v2.4c0 1.1-.8 1.9-1.9 1.9H6.9c-1.1 0-1.9-.8-1.9-1.9V7.9c0-2.6 1.7-4.9 4-6.6ZM5.4 4.8 2.8 7.1l2.4.6M12.6 4.8 15.2 7.1l-2.4.6M6.3 12.2c-.3 1.5-1.3 2.4-1.3 3.8M9 12.3v4.3M11.7 12.2c.3 1.5 1.3 2.4 1.3 3.8"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="7.4" cy="8.5" r="0.75" fill="currentColor" />
        <circle cx="10.6" cy="8.5" r="0.75" fill="currentColor" />
      </svg>
      <span className={styles.name}>squid-pdf</span>
    </span>
  );
}
