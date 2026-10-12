// Find and replace, all in the browser: every span that reads the words found, how many of those
// places a change won't look like the text around them (Rule 1, before "Change all"), and the
// change itself, one step in the history so one undo puts every place back.
import type { SpanInfo } from "../api/types";
import { FIND_DELAY_MS } from "./constants";
import { change, type Editor, type EditorState, spoken } from "./editor";
import { fitOf, runsPast } from "./fit";
import type { Reading } from "./project";
import { finish } from "./typing";
import { counted } from "./words";

export interface FindState {
  readonly open: boolean;
  readonly query: string; // what's typed in "Find", echoed at once
  readonly replacement: string; // what's typed in "Change to"
  readonly searched: string; // what was found, once typing in "Find" stopped
  readonly hits: ReadonlyMap<number, ReadonlySet<string>>; // by page: the spans found, for their marks
  readonly changed: number | null; // how many places "Change all" just changed, until typing again
  readonly opened: number; // counted: each open puts focus in "Find", even when it's open already
  readonly closed: number; // counted: closing from inside the bar puts focus back on the bar's "Find"
}

export const FIND_CLOSED: FindState = {
  open: false,
  query: "",
  replacement: "",
  searched: "",
  hits: new Map(),
  changed: null,
  opened: 0,
  closed: 0,
};

/** One place found: its span, and what it reads now. */
interface Found {
  readonly span: SpanInfo;
  readonly text: string;
}

/** Every span that reads `searched` now, in the document's order. A redaction or a form field takes no replace. */
function foundIn(spans: readonly SpanInfo[], { reading, searched }: { reading: Reading; searched: string }): Found[] {
  if (searched === "") return [];
  const found: Found[] = [];
  for (const span of spans) {
    const now = reading.spans.get(span.id);
    if (span.form_field || now?.redacted) continue;
    const text = now?.text ?? span.text;
    if (text.includes(searched)) found.push({ span, text });
  }
  return found;
}

/** The spans found, by page, keeping each page's set where it's the same, so only pages that changed redraw. */
function hitsOf(found: readonly Found[], before: FindState["hits"]): FindState["hits"] {
  const pages = new Map<number, Set<string>>();
  for (const { span } of found) pages.set(span.page, (pages.get(span.page) ?? new Set()).add(span.id));
  const same = (a: ReadonlySet<string> | undefined, b: ReadonlySet<string>) =>
    a !== undefined && a.size === b.size && [...b].every((id) => a.has(id));
  return new Map(
    [...pages].map(([page, ids]) => [page, same(before.get(page), ids) ? (before.get(page) ?? ids) : ids]),
  );
}

/** The find bar found again in `reading`: after any change to the history, while it's open. */
export function refound(state: Pick<EditorState, "find" | "doc">, reading: Reading): FindState {
  const { find, doc } = state;
  const found = foundIn(doc.spans, { reading, searched: find.searched });
  return { ...find, hits: hitsOf(found, find.hits) };
}

/** How many places are found, and how many of them a change won't keep looking like the text around them. */
export function findCount(state: EditorState): { places: number; unlike: number } {
  const { find, doc, reading, layout } = state;
  const found = foundIn(doc.spans, { reading, searched: find.searched });
  const unlike = found.filter(({ span, text }) => {
    // Drawn in a similar font, or set so an edit won't match: whatever the words.
    if (span.fidelity === "substitute" || span.why !== null) return true;
    const font = layout.fonts.get(span.font);
    if (find.replacement === "" || font === undefined) return false;
    // The live check typing uses, on the words as they'd read changed.
    const fit = fitOf(span, { font, text: text.replaceAll(find.searched, find.replacement), rules: doc.fit });
    return fit.missing.length > 0 || runsPast(fit.deltaPt, fit.roomPt, doc.fit);
  }).length;
  return { places: found.length, unlike };
}

/** The count as the bar says it, before a change: "14 places · 3 will look different". */
export function countSaid({ places, unlike }: { places: number; unlike: number }): string {
  if (places === 0) return "Nothing matches";
  const where = counted(places, { one: "place", other: "places" });
  return unlike === 0 ? `${where} · all look identical` : `${where} · ${unlike} will look different`;
}

/** Open the bar, or put focus back in "Find" if it's open. Words being typed on the page are kept first. */
export function openFind(editor: Editor): void {
  finish(editor, true);
  const state = editor.store.get();
  const find = refound(state, state.reading);
  editor.store.set({ find: { ...find, open: true, opened: find.opened + 1 } });
}

/** Close the bar and clear its marks; `fromInside` puts focus back on the bar's "Find". */
export function closeFind(editor: Editor, { fromInside = false }: { fromInside?: boolean } = {}): void {
  clearTimeout(editor.finding.timer);
  const { find } = editor.store.get();
  editor.store.set({ find: { ...find, open: false, hits: new Map(), closed: find.closed + (fromInside ? 1 : 0) } });
}

/** Typing in "Find": echoed at once, and found once typing stops for a moment. */
export function typeQuery(editor: Editor, query: string): void {
  const { find } = editor.store.get();
  editor.store.set({ find: { ...find, query, changed: null } });
  clearTimeout(editor.finding.timer);
  editor.finding.timer = setTimeout(() => search(editor), FIND_DELAY_MS);
}

/** Typing in "Change to": the count follows at once, since it finds nothing new. */
export function typeReplacement(editor: Editor, replacement: string): void {
  const { find } = editor.store.get();
  editor.store.set({ find: { ...find, replacement, changed: null } });
}

/** Find what's typed, mark it, and say the count. */
function search(editor: Editor): void {
  const state = editor.store.get();
  const find = refound({ ...state, find: { ...state.find, searched: state.find.query } }, state.reading);
  editor.store.set({ find });
  const after = editor.store.get();
  if (find.searched !== "") editor.store.set({ said: spoken(countSaid(findCount(after))) });
}

/** Change every place found, as one step in the history. Needs words to change to: emptying a span is redaction's. */
export function changeAll(editor: Editor): void {
  finish(editor, true);
  const state = editor.store.get();
  const { find } = state;
  // What's typed but not yet found is what the user sees in "Find": find it first.
  const searched = find.query;
  const found = foundIn(state.doc.spans, { reading: state.reading, searched });
  if (found.length === 0 || find.replacement === "") return;
  clearTimeout(editor.finding.timer);
  const edits = found.map(({ span, text }) => ({
    kind: "replace" as const,
    span_id: span.id,
    text: text.replaceAll(searched, find.replacement),
  }));
  const where = counted(found.length, { one: "place", other: "places" });
  change(editor, { kind: "add", edits }, (reading) => ({
    said: spoken(`Changed ${where}.`),
    find: { ...refound({ ...state, find: { ...find, searched } }, reading), changed: found.length },
  }));
}
