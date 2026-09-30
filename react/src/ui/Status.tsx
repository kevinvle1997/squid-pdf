/** What a screen reader hears as it changes, and nobody sees: for what the page doesn't show. */
export function Status({ children }: { children: string }) {
  return (
    <p className="vh" role="status">
      {children}
    </p>
  );
}
