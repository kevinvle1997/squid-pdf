// Find and replace, all in the browser: the spans that read the words, how many a change won't keep
// looking like the text around them (Rule 1), and Change all, one step in the history.
import type { FitInfo, FitRules, Replace, Sentences, SpanInfo, Strategy } from "../api/types";
import { FIND_DELAY_MS } from "./constants";
import { announcing, change, type Editor, type EditorState, scrollingTo } from "./editor";
import { fitOf, type LineScale, lineWidthPt, runsPast, settingOf, wayDrawn } from "./fit";
import { type FindOptions, type Match, matchesIn, replacedIn } from "./matching";
import type { PageState } from "./page";
import { differing, type Reading, textIn } from "./project";
import type { Drawn } from "./render";
import { type FontsByObject, fontOf } from "./spanFonts";
import { finish, isEmpty, keptEditOf, typingEnd } from "./typing";
import { editVerdictOf } from "./verdicts";
import { fillCounted, sentenceOrNothing } from "./words";

/** The find bar: what's typed in it, and what it found. */
export interface FindState extends FindOptions {
  readonly open: boolean;
  readonly query: string; // what's typed in "Find", echoed at once
  readonly replacement: string; // what's typed in "Change to", echoed at once
  readonly judgedReplacement: string; // what the count judges each place's change by, once typing in "Change to" stopped
  readonly announcedCount: string; // the count as last announced: "Change to" announces it again only once it differs
  readonly searched: string; // what was found, once typing in "Find" stopped
  readonly matches: ReadonlyMap<number, ReadonlyMap<string, readonly Match[]>>; // by page: each span found, and where in what it reads
  readonly current: string | null; // the place gone to with Enter, by span, while it's still found
  readonly changed: number | null; // how many places "Change all" just changed, until anything else changes
  readonly opened: number; // counted: each open puts focus in "Find", even when it's open already
  readonly closed: number; // counted: closing from inside the bar puts focus back on the bar's "Find"
  readonly changedAll: number; // counted while open: each Change all puts focus in "Change to", since it can turn off under it
}

/** The find bar before it's opened. */
export const FIND_CLOSED: FindState = {
  open: false,
  matchCase: false,
  wholeWords: false,
  query: "",
  replacement: "",
  judgedReplacement: "",
  announcedCount: "",
  searched: "",
  matches: new Map(),
  current: null,
  changed: null,
  opened: 0,
  closed: 0,
  changedAll: 0,
};

/** One span found: the span, what it reads now and the way out it's drawn with, and where in that the words match. */
interface Found {
  readonly span: SpanInfo;
  readonly text: string;
  readonly strategy: Strategy | undefined;
  readonly matches: readonly Match[];
}

/**
 * Every span that reads `find.searched` in `reading`, `typed` in place of its span's words, in the order shown. None
 * on a page left out of the download, which the file won't have; a redaction or a form field takes no replace.
 */
function foundIn(
  spans: readonly SpanInfo[],
  { reading, find, typed }: { reading: Reading; find: FindState; typed: Replace | null },
): Found[] {
  if (find.searched === "") return [];
  const found: Found[] = [];
  for (const span of spans) {
    const now = reading.spans.get(span.id);
    if (reading.deleted.has(span.page) || span.form_field || now?.redacted) continue;
    const kept = typed?.span_id === span.id ? typed : undefined;
    const text = kept?.text ?? textIn(reading, span);
    const strategy = kept === undefined ? now?.strategy : kept.strategy;
    const matches = matchesIn(text, find.searched, find);
    if (matches.length > 0) found.push({ span, text, strategy, matches });
  }
  return found;
}

/** The places found as Change all would change them: in the words being typed on the page, as typing keeps them. */
function foundNow(state: EditorState): Found[] {
  return foundIn(state.doc.spans, { reading: state.reading, find: state.find, typed: keptEditOf(state) });
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
 * How wide a span's line is drawn, in points: by the server's fit once its render drew these words, else as the
 * browser previews them, its own words set at `scale`, its way out. Without the font's widths, as wide as its box.
 */
export function drawnWidthOf(
  span: SpanInfo,
  {
    text,
    glyphs,
    scale,
    fit,
  }: { text: string; glyphs: Record<string, number> | undefined; scale: LineScale; fit: FitInfo | undefined },
): number {
  const boxPt = span.bbox.x1 - span.bbox.x0;
  // The server's render: a way out ends the line where the original did, as typed it runs on by the fit's difference.
  if (fit !== undefined) return fit.strategy === "as-is" ? boxPt + fit.delta_pt : boxPt;
  const typedPt = glyphs === undefined ? 0 : lineWidthPt(span, text, glyphs);
  const originalPt = glyphs === undefined ? 0 : lineWidthPt(span, span.text, glyphs);
  if (typedPt <= 0 || originalPt <= 0) return boxPt;
  // The original's box, grown or shrunk as the words drawn in it are: measured against the box, not the widths alone.
  return (boxPt * typedPt * scale.size * scale.scaleX) / originalPt;
}

/**
 * Where each match sits across a line `widePt` wide, in points: its share by the widths of the letters
 * before and in it, so the words found are marked, not the whole line.
 */
export function matchesAcross(
  span: SpanInfo,
  {
    text,
    matches,
    glyphs,
    widePt,
  }: { text: string; matches: readonly Match[]; glyphs: Record<string, number> | undefined; widePt: number },
): { x0: number; x1: number }[] {
  const whole = glyphs === undefined ? 0 : lineWidthPt(span, text, glyphs);
  // Measured where the font has widths; else by letter count, the best guess without them.
  const share = (index: number) =>
    glyphs !== undefined && whole > 0
      ? lineWidthPt(span, text.slice(0, index), glyphs) / whole
      : index / Math.max(text.length, 1);
  const at = (index: number) => span.bbox.x0 + share(index) * widePt;
  return matches.map(({ start, end }) => ({ x0: at(start), x1: at(end) }));
}

/** Where find's tints sit on a page: each match across its line as drawn, with its span; none on the span typed in. */
export function tintsOn(
  page: Pick<PageState, "spans" | "edits" | "drawnFrom" | "fits" | "fonts" | "rules" | "typingIn">,
  matches: ReadonlyMap<string, readonly Match[]>,
): { span: SpanInfo; x0: number; x1: number }[] {
  const readings = new Map(page.edits?.spans.map((reading) => [reading.span.id, reading]));
  // Drawn by the browser until the server's render has these words: its fit is for words it drew before.
  const previewed = new Set(differing(page.edits, page.drawnFrom).map(({ span }) => span.id));
  return page.spans.flatMap((span) => {
    const inSpan = matches.get(span.id);
    if (inSpan === undefined || span.id === page.typingIn) return [];
    const reading = readings.get(span.id);
    const text = reading?.text ?? span.text;
    const font = fontOf(page.fonts, span);
    const glyphs = font?.glyphs;
    const scale = settingOf(span, { font, text, asked: reading?.strategy, rules: page.rules });
    const fit = reading?.replaced && !previewed.has(span.id) ? page.fits[span.id] : undefined;
    const widePt = drawnWidthOf(span, { text, glyphs, scale, fit });
    return matchesAcross(span, { text, matches: inSpan, glyphs, widePt }).map(({ x0, x1 }) => ({ span, x0, x1 }));
  });
}

/** The find bar's marks found again in `reading`; the place gone to stays while it's still found. */
function refound(state: Pick<EditorState, "find" | "doc">, reading: Reading): FindState {
  const { find, doc } = state;
  // The span typed in shows no marks (`tintsOn`), so its saved words do here.
  const found = foundIn(doc.spans, { reading, find, typed: null });
  const current = found.some(({ span }) => span.id === find.current) ? find.current : null;
  return { ...find, matches: matchesOf(found, find.matches), current };
}

/** The places found, by span, in the document's order: those the count counts. */
function placesIn(state: EditorState): string[] {
  return foundNow(state).map(({ span }) => span.id);
}

/** Where the place gone to is among those the count counts, counting from 1; null before one is. */
export function placeAt(state: EditorState): number | null {
  const index = state.find.current === null ? -1 : placesIn(state).indexOf(state.find.current);
  return index < 0 ? null : index + 1;
}

/** The place `step` from the one gone to, round from the last to the first; the first or last when none is yet. */
function placeAfter(places: readonly string[], current: string | null, step: 1 | -1): string | undefined {
  const index = current === null ? -1 : places.indexOf(current);
  if (index < 0) return step === 1 ? places[0] : places.at(-1);
  return places[(index + step + places.length) % places.length];
}

/** Go to the next place found, or the one before (`step` -1): marked, brought into the middle of the view, and announced. */
export function stepFind(editor: Editor, step: 1 | -1): void {
  const state = editor.store.get();
  if (!state.find.open) return;
  // What's typed but not yet found or counted is what the user sees: caught up first, its count heard with the place.
  clearTimeout(editor.finding.timer);
  const { find, heard } = caughtUpCount(state);
  const places = placesIn({ ...state, find });
  const current = placeAfter(places, find.current, step);
  if (current === undefined) {
    editor.store.set(hearing({ find, heard }));
    return;
  }
  const place = `${places.indexOf(current) + 1} of ${places.length}`;
  editor.store.set({
    find: { ...find, current },
    scrollTo: scrollingTo(current, "center"),
    announcement: announcing(heard === "" ? place : [heard, place].join(state.doc.sentences.join_parts)),
  });
}

/** The find bar finding and counting what its fields show now, though typing in them may not have stopped. */
function caughtUp(find: FindState): FindState {
  return { ...find, searched: find.query, judgedReplacement: find.replacement };
}

/** The find bar once the history changes: found again while it's open, and what Change all did no longer shown. */
export function findAfterChange(state: Pick<EditorState, "find" | "doc">, reading: Reading): FindState {
  const found = state.find.open ? refound(state, reading) : state.find;
  // What Change all did shows until anything else changes the history: undo would take that instead.
  return found.changed === null ? found : { ...found, changed: null };
}

/** How many places are found, how many of them will look different, and how many Change all leaves as they are. */
export interface FindCount {
  readonly places: number;
  readonly mismatches: number;
  readonly leftAsTheyAre: number; // the change would empty them, so Change all leaves them as they are, as typing would
}

/** Whether changing the words found in a span would empty it, by typing's own test; nothing to change to yet doesn't. */
function wouldEmpty({ text, matches }: Found, replacement: string): boolean {
  return replacement !== "" && isEmpty(replacedIn(text, matches, replacement));
}

/** Whether changing the words found in a span leaves it set apart from the text around it, as the chip says of that edit. */
function looksDifferent(
  { span, text, strategy, matches }: Found,
  { drawn, fonts, replacement, rules }: { drawn: Drawn; fonts: FontsByObject; replacement: string; rules: FitRules },
): boolean {
  const font = fontOf(fonts, span);
  // Its font unknown, the chip works nothing out and shows the span's own verdict: so does the count.
  if (font === undefined) return span.fidelity !== "exact";
  // Nothing to change to yet: its words as they read now, as the chip judges them when an edit of it starts.
  const judged =
    replacement === "" ? { text, strategy } : { text: replacedIn(text, matches, replacement), strategy: undefined };
  if (editVerdictOf(drawn, { span, font, ...judged }).fidelity !== "exact") return true;
  // Past its room with no way out taken, the line runs on: the chip warns of that too. Change all takes none.
  const fit = fitOf(span, { font, text: judged.text, rules });
  return runsPast(fit.deltaPt, fit.roomPt, rules) && wayDrawn(judged.strategy, fit) === "as-is";
}

/** The find bar's count now, found as Change all finds: in the words being typed, as typing keeps them. */
export function findCount(state: EditorState): FindCount {
  const { find, doc, layout, drawn } = state;
  const found = foundNow(state);
  const changing = found.filter((each) => !wouldEmpty(each, find.judgedReplacement));
  const judging = { drawn, fonts: layout.fonts, replacement: find.judgedReplacement, rules: doc.fit };
  const mismatches = changing.filter((each) => looksDifferent(each, judging)).length;
  return { places: found.length, mismatches, leftAsTheyAre: found.length - changing.length };
}

/** The count before a change, as the find bar shows it, in the server's words: the part that warns kept apart for --warn. */
export function countShown(
  { places, mismatches, leftAsTheyAre }: FindCount,
  sentences: Sentences,
): { plain: string; warned: string } {
  if (places === 0) return { plain: sentenceOrNothing(sentences.find_nothing), warned: "" };
  if (leftAsTheyAre === places) return { plain: "", warned: fillCounted(sentences.find_empty, places) };
  const identical = mismatches === 0 && leftAsTheyAre === 0;
  if (identical) return { plain: fillCounted(sentences.find_identical, places), warned: "" };
  if (mismatches === places) return { plain: "", warned: fillCounted(sentences.find_different, places) };
  const plain = fillCounted(sentences.find_places, places);
  const different = mismatches === 0 ? [] : [fillCounted(sentences.find_some_different, mismatches)];
  const empty = leftAsTheyAre === 0 ? [] : [fillCounted(sentences.find_some_empty, leftAsTheyAre)];
  const warnings = [...different, ...empty];
  // A reading persisted by an earlier build may lack any part: then none shows.
  if (plain === "" || warnings.includes("")) return { plain: "", warned: "" };
  return { plain, warned: warnings.join(sentences.join_parts) };
}

/** The count as announced, in the words shown: "14 places, 3 of them will look different". */
export function countSentence(count: FindCount, sentences: Sentences): string {
  const { plain, warned } = countShown(count, sentences);
  return `${plain}${warned}`;
}

/** What Change all did, as shown and as announced, in the server's words: "Changed 3 places." */
export function changedSentence(changed: number, sentences: Sentences): string {
  return fillCounted(sentences.changed_places, changed);
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
  const closed = find.closed + (fromInside ? 1 : 0);
  // What's typed but not yet found or counted is caught up, so the bar opens again on what its fields show.
  // Opened again, it puts focus in "Find", as every open does, not where the last Change all left it.
  const ended = { open: false, matches: new Map(), current: null, closed, changedAll: 0 };
  editor.store.set({ find: { ...caughtUp(find), ...ended } });
}

/** Typing in "Find": echoed at once, and found once typing stops for a moment. */
export function typeQuery(editor: Editor, query: string): void {
  const { find } = editor.store.get();
  editor.store.set({ find: { ...find, query, changed: null } });
  clearTimeout(editor.finding.timer);
  editor.finding.timer = setTimeout(() => search(editor), FIND_DELAY_MS);
}

/** Typing in "Change to": echoed at once, and counted once typing stops for a moment, as "Find" is found. */
export function typeReplacement(editor: Editor, replacement: string): void {
  const { find } = editor.store.get();
  editor.store.set({ find: { ...find, replacement, changed: null } });
  clearTimeout(editor.finding.timer);
  editor.finding.timer = setTimeout(() => countReplacement(editor), FIND_DELAY_MS);
}

/** Count what "Change to" shows: the count, and its announcement where it differs from the last, in one update. */
function countReplacement(editor: Editor): void {
  editor.store.set(hearing(caughtUpCount(editor.store.get())));
}

/** The find bar as it finds and counts, and its count to hear with it: "" where there's none. */
interface Counted {
  readonly find: FindState;
  readonly heard: string;
}

/** The find bar caught up with what its fields show: a search's count heard always, a "Change to"'s once it differs. */
function caughtUpCount(state: EditorState): Counted {
  const { find } = state;
  // The fields share one wait: what "Find" shows but hasn't found is found too.
  if (find.searched !== find.query) return searched(state);
  if (find.judgedReplacement !== find.replacement) return counted(state, caughtUp(find));
  return { find, heard: "" };
}

/** The find bar as `find`, its count heard where it differs from the one last heard. */
function counted(state: EditorState, find: FindState): Counted {
  const sentence = countSentence(findCount({ ...state, find }), state.doc.sentences);
  // Nothing found yet shows no count to hear; one heard already isn't heard again.
  if (find.searched === "" || sentence === find.announcedCount) return { find, heard: "" };
  return { find: { ...find, announcedCount: sentence }, heard: sentence };
}

/** The find bar, and its count as the announcement where there's one to hear. */
function hearing({ find, heard }: Counted): Pick<EditorState, "find"> & Partial<EditorState> {
  return heard === "" ? { find } : { find, announcement: announcing(heard) };
}

/** Match case on or off: found again at once, since nothing is being typed. */
export function setMatchCase(editor: Editor, matchCase: boolean): void {
  search(editor, { matchCase });
}

/** Whole words on or off: found again at once, as Match case is. */
export function setWholeWords(editor: Editor, wholeWords: boolean): void {
  search(editor, { wholeWords });
}

/** Find what "Find" shows, matched as `asked` says: marked, and the count announced, in one update. */
function search(editor: Editor, asked: Partial<FindOptions> = {}): void {
  editor.store.set(hearing(searched(editor.store.get(), asked)));
}

/** The find bar finding what "Find" shows from the top, matched as `asked` says, and its count to hear. */
function searched(state: EditorState, asked: Partial<FindOptions> = {}): Counted {
  const searching = { ...caughtUp({ ...state.find, ...asked }), changed: null, current: null };
  const find = refound({ ...state, find: searching }, state.reading);
  if (find.searched === "") return { find, heard: "" };
  const heard = countSentence(findCount({ ...state, find }), state.doc.sentences);
  return { find: { ...find, announcedCount: heard }, heard };
}

/** Change every place found, as one step in the history. Words being typed on the page are kept first, as their own. */
export function changeAll(editor: Editor): void {
  const state = editor.store.get();
  const { typed, ended } = typingEnd(state, true);
  // What's typed but not yet found or counted is what the user sees: find and count it first.
  const find = { ...caughtUp(state.find), changedAll: state.find.changedAll + 1 };
  // Nothing to change to, since emptying a span is redaction's job. Found as the count finds, in the words typed.
  const found = find.replacement === "" ? [] : foundNow({ ...state, find });
  // A span it would leave empty stays as it is, as typing leaves it.
  const changing = found.filter((each) => !wouldEmpty(each, find.replacement));
  clearTimeout(editor.finding.timer);
  // Nothing changes: the count, caught up, says why.
  if (changing.length === 0) {
    change(editor, typed, (next) => ({
      ...ended,
      ...hearing(counted(next, refound({ ...next, find }, next.reading))),
    }));
    return;
  }
  const edits = changing.map(({ span, text, matches }) => ({
    kind: "replace" as const,
    span_id: span.id,
    text: replacedIn(text, matches, find.replacement),
  }));
  // What changed is heard last, not a count: the next count is heard, whatever it says.
  change(editor, [typed, { kind: "add", edits }], (next) => ({
    ...ended,
    announcement: announcing(changedSentence(changing.length, state.doc.sentences)),
    find: { ...refound({ ...next, find }, next.reading), changed: changing.length, announcedCount: "" },
  }));
}
