import styles from "./Wordmark.module.css";

/** The squid and the name, as in the editor mockup. */
export function Wordmark() {
  return (
    <span className={styles.wordmark}>
      <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
        <path
          d="M9 1.75c2.6 0 4.5 1.9 4.5 4.4 0 1.8-.7 2.6-.7 3.7 0 .8.6 1.2.6 2 0 .7-.5 1.2-1.1 1.2-.6 0-1.1-.5-1.1-1.2M9 1.75c-2.6 0-4.5 1.9-4.5 4.4 0 1.8.7 2.6.7 3.7 0 .8-.6 1.2-.6 2 0 .7.5 1.2 1.1 1.2.6 0 1.1-.5 1.1-1.2M7.7 11.2c0 1.6.2 3 .2 4M10.3 11.2c0 1.6-.2 3-.2 4"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="7.1" cy="6.3" r="1" fill="currentColor" />
        <circle cx="10.9" cy="6.3" r="1" fill="currentColor" />
      </svg>
      squid-pdf
    </span>
  );
}
