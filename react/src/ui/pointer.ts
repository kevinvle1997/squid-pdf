const COARSE = "(pointer: coarse)";

export function coarsePointer(): boolean {
  return matchMedia(COARSE).matches;
}

export function onPointerChange(listener: () => void) {
  const query = matchMedia(COARSE);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}
