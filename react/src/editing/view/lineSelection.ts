// Where the caret and a selection are in a line's field, as offsets into its runs' words read as one: an element
// per run (`data-run`), each holding its words. Read off the DOM, which node hasn't, so tested in the browser.

/** Whether this browser's editable text can take plain text alone: then no markup ever comes in with a paste. */
export function takesPlainTextOnly(): boolean {
  const probe = document.createElement("div");
  try {
    // A browser without it refuses the value: some throw, others leave it as it was.
    probe.contentEditable = "plaintext-only";
  } catch {
    return false;
  }
  return probe.contentEditable === "plaintext-only";
}

/** The field's runs, in order. */
function runsIn(field: HTMLElement): HTMLElement[] {
  return [...field.querySelectorAll<HTMLElement>("[data-run]")];
}

/** How far into the field's words a point in its DOM is: in a run's words, or between the field's runs. */
export function offsetAt(field: HTMLElement, node: Node, offset: number): number {
  const runs = runsIn(field);
  // Between runs, `offset` counts the field's own children before the point.
  if (node === field) return runs.slice(0, offset).reduce((total, run) => total + (run.textContent ?? "").length, 0);
  let before = 0;
  for (const run of runs) {
    const length = (run.textContent ?? "").length;
    if (run === node) return before + (offset === 0 ? 0 : length);
    if (run.contains(node)) return before + offset;
    before += length;
  }
  return before;
}

/** The field's selection, as its start and end in the field's words: none while it's elsewhere. */
export function selectionIn(field: HTMLElement): [number, number] | null {
  const selection = field.ownerDocument.getSelection();
  if (selection === null || selection.rangeCount === 0) return null;
  const range = selection.getRangeAt(0);
  if (!field.contains(range.startContainer) || !field.contains(range.endContainer)) return null;
  return [
    offsetAt(field, range.startContainer, range.startOffset),
    offsetAt(field, range.endContainer, range.endOffset),
  ];
}

/** What an edit about to be made replaces, as its start and end in the field's words: the browser's own ranges, else the selection. */
export function rangeEditedIn(field: HTMLElement, event: InputEvent): [number, number] | null {
  const [range] = event.getTargetRanges();
  if (range === undefined) return selectionIn(field);
  return [
    offsetAt(field, range.startContainer, range.startOffset),
    offsetAt(field, range.endContainer, range.endOffset),
  ];
}

/**
 * The DOM point `offset` into the field's words falls at: in the run the words before it end, so the caret between
 * two runs stands where typing goes, in the one before. An emptied run is passed over, as typing passes it.
 */
function pointAt(field: HTMLElement, offset: number): [Node, number] {
  let before = 0;
  for (const run of runsIn(field)) {
    const words = run.firstChild;
    const length = (run.textContent ?? "").length;
    if (words !== null && length > 0 && offset <= before + length) return [words, offset - before];
    before += length;
  }
  return [field, 0];
}

/** Select `start` to `end` of the field's words: a caret where they meet. */
export function selectIn(field: HTMLElement, start: number, end: number): void {
  const selection = field.ownerDocument.getSelection();
  if (selection === null) return;
  const [startNode, startOffset] = pointAt(field, start);
  const [endNode, endOffset] = pointAt(field, end);
  selection.setBaseAndExtent(startNode, startOffset, endNode, endOffset);
}

/** The words each run of the field holds now, as an input method left them: a run gone holds none. */
export function runsWordsIn(field: HTMLElement, count: number): string[] {
  const words = Array.from({ length: count }, () => "");
  for (const run of runsIn(field)) words[Number(run.dataset.run)] = run.textContent ?? "";
  return words;
}

/** Which run of the field lies under `xPx` from its left edge, else the nearest: where something dropped on it landed. */
export function runUnder(field: HTMLElement, xPx: number): number {
  const left = field.getBoundingClientRect().left + xPx;
  const away = (run: HTMLElement) => {
    const box = run.getBoundingClientRect();
    return Math.max(box.left - left, left - box.right, 0);
  };
  const nearest = runsIn(field).reduce<HTMLElement | undefined>(
    (best, run) => (best === undefined || away(run) < away(best) ? run : best),
    undefined,
  );
  return Number(nearest?.dataset.run ?? 0);
}
