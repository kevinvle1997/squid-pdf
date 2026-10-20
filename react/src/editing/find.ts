// Find and replace, all in the browser: the spans that read the words, how many a change won't keep
// looking like the text around them (Rule 1), and Change all, one step in the history.
import type { FitRules, FontInfo, SpanInfo } from "../api/types";
import { FIND_DELAY_MS } from "./constants";
import { change, changed, type Editor, type EditorState, spoken } from "./editor";
import { fitOf, lineWidthPt, runsPast } from "./fit";
import { wontMatch } from "./marks";
import { type FindOptions, type Match, matchesIn, replacedIn } from "./matching";
import { type Reading, textIn } from "./project";
import { finish, typingEnd } from "./typing";
import { counted } from "./words";

/** The find bar: what's typed in it, and what it found. */
export interface FindState extends FindOptions {
  readonly open: boolean;
  readonly query: string; // what's typed in "Find", echoed at once
  readonly replacement: string; // what's typed in "Change to"
  readonly searched: string; // what was found, once typing in "Find" stopped
  readonly matches: ReadonlyMap<number, ReadonlyMap<string, readonly Match[]>>; // by page: each span found, and where in what it reads
  readonly changed: number | null; // how many places "Change all" just changed, until anything else changes
  readonly opened: number; // counted: each open puts focus in "Find", even when it's open already
  readonly closed: number; // counted: closing from inside the bar puts focus back on the bar's "Find"
}

/** The find bar before it's opened. */
export const FIND_CLOSED: FindState = {
  open: false,
  matchCase: false,
  wholeWords: false,
  query: "",
  replacement: "",
  searched: "",
  matches: new Map(),
  changed: null,
  opened: 0,
  closed: 0,
};

/** One span found: the span, what it reads now, and where in that the words match. */
interface Found {
  readonly span: SpanInfo;
  readonly text: string;
  readonly matches: readonly Match[];
}

/** Every span that reads `find.searched` now, in the document's order. A redaction or a form field takes no replace. */
function foundIn(spans: readonly SpanInfo[], { reading, find }: { reading: Reading; find: FindState }): Found[] {
  if (find.searched === "") return [];
  const found: Found[] = [];
  for (const span of spans) {
    const now = reading.spans.get(span.id);
    if (span.form_field || now?.redacted) continue;
    const text = textIn(reading, span);
    const matches = matchesIn(text, find.searched, find);
    if (matches.length > 0) found.push({ span, text, matches });
  }
  return found;
}

/** Whether a span's matches are the same as before. */
function sameMatches(a: readonly Match[] | undefined, b: readonly Match[]): boolean {
  return (
    a !== undefined &&
    a.length === b.length &&
    b.every((match, i) => match.start === a[i]?.start && match.end === a[i]?.end)
  );
}

/** Whether a page's matches, span by span, are the same as before. */
function samePageMatches(a: ReadonlyMap<string, readonly Match[]>, b: ReadonlyMap<string, readonly Match[]>): boolean {
  return a.size === b.size && [...b].every(([spanId, matches]) => sameMatches(a.get(spanId), matches));
}

/** The matches by page and span, keeping each page's map where it's the same, so only pages that changed redraw. */
function matchesOf(found: readonly Found[], before: FindState["matches"]): FindState["matches"] {
  const pages = new Map<number, Map<string, readonly Match[]>>();
  for (const { span, matches } of found) {
    const onPage = pages.get(span.page) ?? new Map<string, readonly Match[]>();
    pages.set(span.page, onPage.set(span.id, matches));
  }
  return new Map(
    [...pages].map(([page, spans]) => {
      const was = before.get(page);
      return [page, was !== undefined && samePageMatches(was, spans) ? was : spans];
    }),
  );
}

/**
 * Where each match sits across its span, in points: its share of the span's width by the widths
 * of the letters before and in it, so the words found are marked, not the whole line.
 */
export function matchesAcross(
  span: SpanInfo,
  { text, matches, glyphs }: { text: string; matches: readonly Match[]; glyphs: Record<string, number> },
): { x0: number; x1: number }[] {
  const whole = lineWidthPt(span, text, glyphs);
  const wide = span.bbox.x1 - span.bbox.x0;
  // Measured where the font has widths; else by letter count, the best guess without them.
  const at = (index: number) =>
    span.bbox.x0 +
    (whole > 0 ? lineWidthPt(span, text.slice(0, index), glyphs) / whole : index / Math.max(text.length, 1)) * wide;
  return matches.map(({ start, end }) => ({ x0: at(start), x1: at(end) }));
}

/** The find bar's matches found again in `reading`. */
function refound(state: Pick<EditorState, "find" | "doc">, reading: Reading): FindState {
  const { find, doc } = state;
  const found = foundIn(doc.spans, { reading, find });
  return { ...find, matches: matchesOf(found, find.matches) };
}

/** The find bar searching what "Find" shows now, though typing in it may not have stopped. */
function searchingQuery(find: FindState): FindState {
  return { ...find, searched: find.query };
}

/** The find bar once the history changes: found again while it's open, and what Change all did no longer said. */
export function findAfterChange(state: Pick<EditorState, "find" | "doc">, reading: Reading): FindState {
  const found = state.find.open ? refound(state, reading) : state.find;
  // What Change all did is said until anything else changes the history: undo would take that instead.
  return found.changed === null ? found : { ...found, changed: null };
}

/** How many places are found, and how many of them a change won't keep looking like the text around them. */
export interface FindCount {
  readonly places: number;
  readonly unlike: number;
}

/** Whether changing the words found in a span leaves it unlike the text around it. */
function looksDifferent(
  { span, text, matches }: Found,
  { fonts, replacement, rules }: { fonts: ReadonlyMap<string, FontInfo>; replacement: string; rules: FitRules },
): boolean {
  // Drawn in a similar font, or set so an edit won't match: whatever the words.
  if (wontMatch(span)) return true;
  const font = fonts.get(span.font);
  // Nothing to change to yet, or no widths to check it by.
  if (replacement === "" || font === undefined) return false;
  // The live check typing uses, on the words as they'd read changed.
  const fit = fitOf(span, { font, text: replacedIn(text, matches, replacement), rules });
  return fit.missing.length > 0 || runsPast(fit.deltaPt, fit.roomPt, rules);
}

/** The find bar's count now. */
export function findCount(state: EditorState): FindCount {
  const { find, doc, reading, layout } = state;
  const found = foundIn(doc.spans, { reading, find });
  const judging = { fonts: layout.fonts, replacement: find.replacement, rules: doc.fit };
  return { places: found.length, unlike: found.filter((each) => looksDifferent(each, judging)).length };
}

/** The count before a change, as the find bar shows it: one phrase, the part that warns kept apart for --warn. */
export function countShown({ places, unlike }: FindCount): { plain: string; warned: string } {
  if (places === 0) return { plain: "Nothing matches", warned: "" };
  const where = counted(places, { one: "place", other: "places" });
  // Every place will look like the text around it.
  if (unlike === 0) return { plain: `${where} will look identical`, warned: "" };
  // Not one will.
  if (unlike === places) return { plain: "", warned: `${where} will look different` };
  return { plain: `${where}, `, warned: `${unlike} of them will look different` };
}

/** The count as said, in the words shown: "14 places, 3 of them will look different". */
export function countSaid(count: FindCount): string {
  const { plain, warned } = countShown(count);
  return `${plain}${warned}`;
}

/** What Change all did, as shown and as said: "Changed 3 places." */
export function changedSaid(changed: number): string {
  return `Changed ${counted(changed, { one: "place", other: "places" })}.`;
}

/** Open the bar, or put focus back in "Find" if it's open. Words being typed on the page are kept first. */
export function openFind(editor: Editor): void {
  finish(editor, true, {
    also: (next) => {
      const find = refound(next, next.reading);
      return { find: { ...find, open: true, opened: find.opened + 1 } };
    },
  });
}

/** Close the bar and clear its marks; `fromInside` puts focus back on the bar's "Find". */
export function closeFind(editor: Editor, { fromInside = false }: { fromInside?: boolean } = {}): void {
  clearTimeout(editor.finding.timer);
  const { find } = editor.store.get();
  editor.store.set({ find: { ...find, open: false, matches: new Map(), closed: find.closed + (fromInside ? 1 : 0) } });
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

/** Match case on or off: found again at once, since nothing is being typed. */
export function setMatchCase(editor: Editor, matchCase: boolean): void {
  search(editor, { matchCase });
}

/** Whole words on or off: found again at once, as Match case is. */
export function setWholeWords(editor: Editor, wholeWords: boolean): void {
  search(editor, { wholeWords });
}

/** Find what "Find" shows, matched as `asked` says: marked, and the count said, in one update. */
function search(editor: Editor, asked: Partial<FindOptions> = {}): void {
  const state = editor.store.get();
  const searching = { ...searchingQuery({ ...state.find, ...asked }), changed: null };
  const find = refound({ ...state, find: searching }, state.reading);
  const heard = find.searched !== "" && { said: spoken(countSaid(findCount({ ...state, find }))) };
  editor.store.set({ find, ...heard });
}

/** Change every place found, as one step in the history. Words being typed on the page are kept first, as their own. */
export function changeAll(editor: Editor): void {
  const state = editor.store.get();
  const { typed, ended } = typingEnd(state, true);
  // Found in the words as typed: their step comes first.
  const typing = changed(state, [typed]);
  // What's typed but not yet found is what the user sees in "Find": find it first.
  const find = searchingQuery(typing.find);
  // Nothing to change to, since emptying a span is redaction's job.
  const found = find.replacement === "" ? [] : foundIn(state.doc.spans, { reading: typing.reading, find });
  // Nothing to change: only the typing ends.
  if (found.length === 0) {
    change(editor, typed, ended);
    return;
  }
  clearTimeout(editor.finding.timer);
  const edits = found.map(({ span, text, matches }) => ({
    kind: "replace" as const,
    span_id: span.id,
    text: replacedIn(text, matches, find.replacement),
  }));
  change(editor, [typed, { kind: "add", edits }], (next) => ({
    ...ended,
    said: spoken(changedSaid(found.length)),
    find: { ...refound({ ...next, find }, next.reading), changed: found.length },
  }));
}
