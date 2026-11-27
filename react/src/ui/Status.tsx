/** Drawn anew for each `count`, so the same words announced again are heard again. */
export function Status({ children, count }: { children: string; count: number }) {
  return (
    <p className="vh" role="status">
      <span key={count}>{children}</span>
    </p>
  );
}
