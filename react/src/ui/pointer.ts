// The pointer's kind: a finger has no keys beside it, so nothing is offered for them.

const COARSE = "(pointer: coarse)";

/** Whether the pointer is a finger. */
export function coarsePointer(): boolean {
  return matchMedia(COARSE).matches;
}

/** Calls `listener` when the pointer changes kind, as a tablet docked to a keyboard does; returns how to stop. */
export function onPointerChange(listener: () => void) {
  const query = matchMedia(COARSE);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}
