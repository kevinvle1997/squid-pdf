import styles from "./Wordmark.module.css";

/** The squid and the name, as in the editor mockup. */
export function Wordmark() {
  return (
    <span className={styles.wordmark}>
      <svg width="15" height="15" viewBox="0 0 15 15" aria-hidden="true">
        <path
          d="M7.5 1.3c2.2 0 3.8 1.6 3.8 3.7 0 1.5-.6 2.2-.6 3.1 0 .7.5 1 .5 1.7 0 .6-.4 1-.9 1-.5 0-.9-.4-.9-1M7.5 1.3C5.3 1.3 3.7 2.9 3.7 5c0 1.5.6 2.2.6 3.1 0 .7-.5 1-.5 1.7 0 .6.4 1 .9 1 .5 0 .9-.4.9-1M6.4 9.3c0 1.3.2 2.5.2 3.3M8.6 9.3c0 1.3-.2 2.5-.2 3.3"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.15"
          strokeLinecap="round"
        />
        <circle cx="5.9" cy="5.2" r=".8" fill="currentColor" />
        <circle cx="9.1" cy="5.2" r=".8" fill="currentColor" />
      </svg>
      squid-pdf
    </span>
  );
}
