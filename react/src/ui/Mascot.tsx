import styles from "./Mascot.module.css";

// The squid's outline, drawn twice: once wide in the paper's white, a cut-out's edge that keeps it apart from
// either mat, then in ink. Its mantle, fins at its crown, and five arms, the outer two curling as the wordmark's do.
const SHAPES = [
  { d: "M61 102c-2 12-12 17-15 27-2 7 4 12 10 8", arm: true },
  { d: "M99 102c2 12 12 17 15 27 2 7-4 12-10 8", arm: true },
  { d: "M70 104c-1 12-5 21-3 33", arm: true },
  { d: "M80 104c0 12 2 23 0 36", arm: true },
  { d: "M90 104c1 12 5 21 3 33", arm: true },
  { d: "M63 40 41 58l18 8Z", arm: false },
  { d: "M97 40l22 18-18 8Z", arm: false },
  { d: "M80 10c14 13 26 32 26 54v26c0 8-6 14-14 14H68c-8 0-14-6-14-14V64c0-22 12-41 26-54Z", arm: false },
];

/** The squid that stands on the landing's sheet: paper, cut out and drawn in ink, the same in both themes. */
export function Mascot({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 160 160" aria-hidden="true">
      <g className={styles.edge}>
        {SHAPES.map(({ d, arm }) => (
          <path key={d} d={d} fill={arm ? "none" : undefined} />
        ))}
      </g>
      <g className={styles.ink}>
        {SHAPES.map(({ d, arm }) => (
          <path key={d} d={d} fill={arm ? "none" : undefined} />
        ))}
      </g>
      <g className={styles.eyes}>
        <circle cx="70" cy="78" r="5.5" />
        <circle cx="90" cy="78" r="5.5" />
      </g>
      <g className={styles.glints}>
        <circle cx="71.8" cy="76.2" r="1.7" />
        <circle cx="91.8" cy="76.2" r="1.7" />
      </g>
      <path className={styles.smile} d="M75 90q5 4 10 0" />
    </svg>
  );
}
