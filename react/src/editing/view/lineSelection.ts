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

function runsIn(field: HTMLElement): HTMLElement[] {
  return [...field.querySelectorAll<HTMLElement>("[data-run]")];
}

export function offsetAt(field: HTMLElement, node: Node, offset: number): number {
  const runs = runsIn(field);
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

export function rangeEditedIn(field: HTMLElement, event: InputEvent): [number, number] | null {
  const [range] = event.getTargetRanges();
  if (range === undefined) return selectionIn(field);
  return [
    offsetAt(field, range.startContainer, range.startOffset),
    offsetAt(field, range.endContainer, range.endOffset),
  ];
}

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

export function selectIn(field: HTMLElement, start: number, end: number): void {
  const selection = field.ownerDocument.getSelection();
  if (selection === null) return;
  const [startNode, startOffset] = pointAt(field, start);
  const [endNode, endOffset] = pointAt(field, end);
  selection.setBaseAndExtent(startNode, startOffset, endNode, endOffset);
}

export function runsIntact(field: HTMLElement, count: number): boolean {
  return field.children.length === count && runsIn(field).length === count && !holdsLooseWords(field);
}

function holdsLooseWords(field: HTMLElement): boolean {
  return [...field.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && node.textContent !== "");
}

export function runsWordsIn(field: HTMLElement, count: number): string[] {
  const words = Array.from({ length: count }, () => "");
  for (const run of runsIn(field)) words[Number(run.dataset.run)] = run.textContent ?? "";
  return words;
}

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
