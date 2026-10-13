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

/** How "Find" matches: letters of either case unless `matchCase`, inside a word too unless `wholeWords`. */
export interface FindOptions {
  readonly matchCase: boolean;
  readonly wholeWords: boolean;
}

export interface FindState extends FindOptions {
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
  matchCase: false,
  wholeWords: false,
  query: "",
  replacement: "",
  searched: "",
  hits: new Map(),
  changed: null,
  opened: 0,
  closed: 0,
};

/** Where words were found in a text, from its `start` to its `end`, in UTF-16 units as `slice` takes them. */
export interface Place {
  readonly start: number;
  readonly end: number;
}

// The space, quote and dash a keyboard types, and each letter a page may set instead: a date with a
// no-break space, a name with a curly apostrophe, a range with an en dash. "Find" reads them as typed.
const KEYBOARD: readonly { readonly typed: string; readonly letters: RegExp }[] = [
  { typed: " ", letters: /[\s\u00a0\u2000-\u200a\u202f\u205f\u3000]/u },
  { typed: "'", letters: /[\u2018\u2019\u201a\u201b\u2032\u02bc]/u },
  { typed: '"', letters: /[\u201c-\u201f\u2033]/u },
  { typed: "-", letters: /[\u2010-\u2015\u2212]/u },
];

// Drawn as nothing, so read as nothing: a soft hyphen, a zero-width space or joiner.
const UNSEEN = /[\u00ad\u200b\u2060\ufeff]/u;

// A ligature, such as fi, read as the letters it joins.
const LIGATURE = /[\ufb00-\ufb06]/u;

const WORD_LETTER = /[\p{L}\p{M}\p{N}_]/u;

/** A letter as "Find" reads it. */
function readAs(letter: string, { matchCase }: Pick<FindOptions, "matchCase">): string {
  const typed = KEYBOARD.find(({ letters }) => letters.test(letter))?.typed;
  const read = typed ?? (UNSEEN.test(letter) ? "" : LIGATURE.test(letter) ? letter.normalize("NFKC") : letter);
  return matchCase ? read : read.toLowerCase();
}

/**
 * `replacement` spelled as the page spelled `found`, the words it replaces: where every space, quote or
 * dash of a kind in `found` is the same letter the keyboard can't type, the replacement's are that letter.
 * Why: a no-break space a page set stays one, and a font that draws only that one draws the change.
 */
function spelledAs(found: string, replacement: string): string {
  let spelled = replacement;
  for (const { typed, letters } of KEYBOARD) {
    const set = new Set([...found].filter((letter) => letters.test(letter)));
    const [only] = set;
    if (set.size === 1 && only !== undefined && only !== typed) spelled = spelled.replaceAll(typed, only);
  }
  return spelled;
}

/** `text` as "Find" reads it, and for each unit of that the letter of `text` it came from, `from` to `to`. */
function readOf(text: string, options: Pick<FindOptions, "matchCase">) {
  let read = "";
  const from: number[] = [];
  const to: number[] = [];
  let at = 0;
  for (const letter of text) {
    const piece = readAs(letter, options);
    read += piece;
    for (let unit = 0; unit < piece.length; unit++) {
      from.push(at);
      to.push(at + letter.length);
    }
    at += letter.length;
  }
  return { read, from, to };
}

/** Each place in `text` that reads `searched`, left to right and none overlapping, as `options` match. */
export function placesIn(text: string, searched: string, options: FindOptions): Place[] {
  const wanted = readOf(searched, options).read;
  if (wanted === "") return [];
  const { read, from, to } = readOf(text, options);
  const places: Place[] = [];
  let at = read.indexOf(wanted);
  while (at !== -1) {
    const end = at + wanted.length;
    // Whole letters of the text, never half a ligature; and with Whole words, no letter either side.
    const wholeLetters = from[at - 1] !== from[at] && from[end] !== from[end - 1];
    const apart = !options.wholeWords || !(WORD_LETTER.test(read[at - 1] ?? "") || WORD_LETTER.test(read[end] ?? ""));
    const start = from[at];
    const stop = to[end - 1];
    if (wholeLetters && apart && start !== undefined && stop !== undefined) {
      places.push({ start, end: stop });
      at = read.indexOf(wanted, end);
    } else {
      at = read.indexOf(wanted, at + 1);
    }
  }
  return places;
}

/** `text` with each of `places` changed to `replacement`, spelled as the page spelled what it replaces. */
export function replacedIn(text: string, places: readonly Place[], replacement: string): string {
  let out = "";
  let kept = 0;
  for (const { start, end } of places) {
    out += text.slice(kept, start) + spelledAs(text.slice(start, end), replacement);
    kept = end;
  }
  return out + text.slice(kept);
}

/** One place found: its span, what it reads now, and where in that the words are. */
interface Found {
  readonly span: SpanInfo;
  readonly text: string;
  readonly places: readonly Place[];
}

/** Every span that reads `find.searched` now, in the document's order. A redaction or a form field takes no replace. */
function foundIn(spans: readonly SpanInfo[], { reading, find }: { reading: Reading; find: FindState }): Found[] {
  if (find.searched === "") return [];
  const found: Found[] = [];
  for (const span of spans) {
    const now = reading.spans.get(span.id);
    if (span.form_field || now?.redacted) continue;
    const text = now?.text ?? span.text;
    const places = placesIn(text, find.searched, find);
    if (places.length > 0) found.push({ span, text, places });
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
  const found = foundIn(doc.spans, { reading, find });
  return { ...find, hits: hitsOf(found, find.hits) };
}

/** How many places are found, and how many of them a change won't keep looking like the text around them. */
export function findCount(state: EditorState): { places: number; unlike: number } {
  const { find, doc, reading, layout } = state;
  const found = foundIn(doc.spans, { reading, find });
  const unlike = found.filter(({ span, text, places }) => {
    // Drawn in a similar font, or set so an edit won't match: whatever the words.
    if (span.fidelity === "substitute" || span.why !== null) return true;
    const font = layout.fonts.get(span.font);
    if (find.replacement === "" || font === undefined) return false;
    // The live check typing uses, on the words as they'd read changed.
    const fit = fitOf(span, { font, text: replacedIn(text, places, find.replacement), rules: doc.fit });
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

/** Match case on or off: found again at once, since nothing is being typed. */
export function setMatchCase(editor: Editor, matchCase: boolean): void {
  const { find } = editor.store.get();
  editor.store.set({ find: { ...find, matchCase, changed: null } });
  search(editor);
}

/** Whole words on or off: found again at once, as Match case is. */
export function setWholeWords(editor: Editor, wholeWords: boolean): void {
  const { find } = editor.store.get();
  editor.store.set({ find: { ...find, wholeWords, changed: null } });
  search(editor);
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
  const found = foundIn(state.doc.spans, { reading: state.reading, find: { ...find, searched } });
  if (found.length === 0 || find.replacement === "") return;
  clearTimeout(editor.finding.timer);
  const edits = found.map(({ span, text, places }) => ({
    kind: "replace" as const,
    span_id: span.id,
    text: replacedIn(text, places, find.replacement),
  }));
  const where = counted(found.length, { one: "place", other: "places" });
  change(editor, { kind: "add", edits }, (reading) => ({
    said: spoken(`Changed ${where}.`),
    find: { ...refound({ ...state, find: { ...find, searched } }, reading), changed: found.length },
  }));
}
