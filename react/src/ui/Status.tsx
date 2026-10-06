/**
 * What a screen reader hears as it changes, and nobody sees: for what the page doesn't show.
 * The words are drawn anew for each `count`, so the same words said again are heard again.
 */
export function Status({ children, count }: { children: string; count: number }) {
  return (
    <p className="vh" role="status">
      <span key={count}>{children}</span>
    </p>
  );
}
