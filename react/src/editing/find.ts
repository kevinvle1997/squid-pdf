import type { FitInfo, FitRules, Replace, ReplaceRuns, Sentences, SpanInfo, Strategy } from "../api/types";
import { FIND_DELAY_MS } from "./constants";
import {
  announcing,
  asWired,
  change,
  type Editor,
  type EditorState,
  type Layout,
  readingOf,
  scrollingTo,
  unsplitOf,
} from "./editor";
import { fitOf, type LineScale, lineWidthPt, runsPast, settingOf, wayDrawn } from "./fit";
import { entriesOf, historyReducer, spansOf } from "./history";
import { type DrawnRun, lineDrawnAs } from "./lines";
import { type FindOptions, type Match, matchesIn, replacedIn } from "./matching";
import type { PageState } from "./page";
import { differing, type Reading, type SpanReading, textIn } from "./project";
import type { Drawn } from "./render";
import { type FontsByObject, fontOf } from "./spanFonts";
import { partKey } from "./splits";
import { finish, isEmpty, keptEditOf, typingEnd } from "./typing";
import { editVerdictOf } from "./verdicts";
import { fillCounted, sentenceOrNothing } from "./words";

export interface FindState extends FindOptions {
  readonly open: boolean;
  readonly query: string;
  readonly replacement: string;
  readonly judgedReplacement: string;
  readonly announcedCount: string;
  readonly searched: string;
  readonly matches: ReadonlyMap<number, ReadonlyMap<string, readonly Match[]>>;
  readonly current: string | null;
  readonly changed: number | null;
  readonly opened: number;
  readonly closed: number;
  readonly changedAll: number;
}

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

interface FoundRun {
  readonly span: SpanInfo;
  readonly text: string;
  readonly strategy: Strategy | undefined;
}

/** A place: a line, searched as one text across its runs, or a span on no line. */
interface Found {
  readonly id: string;
  readonly runs: readonly FoundRun[];
  readonly text: string;
  readonly matches: readonly Match[];
}

interface Searching {
  readonly reading: Reading;
  readonly find: FindState;
  readonly typed: Replace | ReplaceRuns | null;
}

function foundIn(layout: Pick<Layout, "ordered" | "lines">, searching: Searching): Found[] {
  if (searching.find.searched === "") return [];
  return placesToSearch(layout, searching).flatMap((runs) => {
    const text = runs.map((run) => run.text).join("");
    const matches = matchesIn(text, searching.find.searched, searching.find);
    const [first] = runs;
    if (matches.length === 0 || first === undefined) return [];
    const id = layout.lines.get(first.span.id)?.id ?? first.span.id;
    return [{ id, runs, text, matches }];
  });
}

function placesToSearch(
  { ordered, lines }: Pick<Layout, "ordered" | "lines">,
  { reading, typed }: Searching,
): FoundRun[][] {
  const places: FoundRun[][] = [];
  for (const span of ordered) {
    const line = lines.get(span.id);
    if (line !== undefined && line.id !== span.id) continue;
    const runs = (line?.spans ?? [span]).map((each) => runOf(each, { reading, typed }));
    // A line with a redaction or a form field in it is searched span by span, leaving those out.
    if (runs.every((run) => run !== null)) places.push(runs);
    else places.push(...runs.flatMap((run) => (run === null ? [] : [[run]])));
  }
  return places;
}

function runOf(span: SpanInfo, { reading, typed }: Omit<Searching, "find">): FoundRun | null {
  const now = reading.spans.get(span.id);
  if (reading.deleted.has(span.page) || span.form_field || now?.redacted) return null;
  const kept = typed !== null && spansOf(typed).includes(span.id) ? typed : undefined;
  const text = (kept === undefined ? undefined : typedTextOf(kept, span.id)) ?? textIn(reading, span);
  const strategy = kept === undefined ? now?.strategy : kept.strategy;
  return { span, text, strategy };
}

/** Each run's share of `matches`, which count from the start of the runs' joined text. */
function runMatchesOf(runs: readonly FoundRun[], matches: readonly Match[]): [FoundRun, Match[]][] {
  let runStart = 0;
  return runs.map((run) => {
    const runEnd = runStart + run.text.length;
    const inRun = matches.flatMap(({ start, end }) =>
      start < runEnd && end > runStart
        ? [{ start: Math.max(start, runStart) - runStart, end: Math.min(end, runEnd) - runStart }]
        : [],
    );
    runStart = runEnd;
    return [run, inRun];
  });
}

/** The runs' words once Change all puts `replacement` in the run where each match starts. */
function runsReplacedIn({ runs, text, matches }: Found, replacement: string): string[] {
  let texts = runs.map((run) => run.text);
  for (const { start, end } of [...matches].reverse()) {
    const typed = replacedIn(text.slice(start, end), [{ start: 0, end: end - start }], replacement);
    texts = runsWithout(texts, { start, end, typed });
  }
  return texts;
}

function runsWithout(texts: readonly string[], { start, end, typed }: Match & { typed: string }): string[] {
  let runStart = 0;
  let placed = false;
  return texts.map((run) => {
    const from = Math.min(Math.max(start - runStart, 0), run.length);
    const to = Math.min(Math.max(end - runStart, 0), run.length);
    const holdsStart = !placed && start < runStart + run.length;
    runStart += run.length;
    if (!holdsStart) return run.slice(0, from) + run.slice(to);
    placed = true;
    return run.slice(0, from) + typed + run.slice(to);
  });
}

function typedTextOf(typed: Replace | ReplaceRuns, spanId: string): string | undefined {
  if (typed.kind === "replace") return typed.text;
  return typed.runs.find((run) => partKey(run.span_id, run.start ?? 0) === spanId)?.text;
}

function foundNow(state: EditorState): Found[] {
  return foundIn(state.layout, { reading: state.reading, find: state.find, typed: keptEditOf(state) });
}

function sameMatches(a: readonly Match[] | undefined, b: readonly Match[]): boolean {
  return (
    a !== undefined &&
    a.length === b.length &&
    b.every((match, i) => match.start === a[i]?.start && match.end === a[i]?.end)
  );
}

function samePageMatches(a: ReadonlyMap<string, readonly Match[]>, b: ReadonlyMap<string, readonly Match[]>): boolean {
  return a.size === b.size && [...b].every(([spanId, matches]) => sameMatches(a.get(spanId), matches));
}

function matchesOf(found: readonly Found[], before: FindState["matches"]): FindState["matches"] {
  const pages = new Map<number, Map<string, readonly Match[]>>();
  for (const { runs, matches } of found) {
    for (const [{ span }, inRun] of runMatchesOf(runs, matches)) {
      if (inRun.length === 0) continue;
      const onPage = pages.get(span.page) ?? new Map<string, readonly Match[]>();
      pages.set(span.page, onPage.set(span.id, inRun));
    }
  }
  return new Map(
    [...pages].map(([page, spans]) => {
      const was = before.get(page);
      return [page, was !== undefined && samePageMatches(was, spans) ? was : spans];
    }),
  );
}

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
  if (fit !== undefined) return fit.strategy === "as-is" ? boxPt + fit.delta_pt : boxPt;
  const typedPt = glyphs === undefined ? 0 : lineWidthPt(span, text, glyphs);
  const originalPt = glyphs === undefined ? 0 : lineWidthPt(span, span.text, glyphs);
  if (typedPt <= 0 || originalPt <= 0) return boxPt;
  return (boxPt * typedPt * scale.size * scale.scaleX) / originalPt;
}

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
  const share = (index: number) =>
    glyphs !== undefined && whole > 0
      ? lineWidthPt(span, text.slice(0, index), glyphs) / whole
      : index / Math.max(text.length, 1);
  const at = (index: number) => span.bbox.x0 + share(index) * widePt;
  return matches.map(({ start, end }) => ({ x0: at(start), x1: at(end) }));
}

export function tintsOn(
  page: Pick<PageState, "spans" | "edits" | "drawnFrom" | "fits" | "fonts" | "rules" | "typingIn" | "layout">,
  matches: ReadonlyMap<string, readonly Match[]>,
): { span: SpanInfo; x0: number; x1: number }[] {
  const readings = new Map(page.edits?.spans.map((reading) => [reading.span.id, reading]));
  const previewed = new Set(differing(page.edits, page.drawnFrom).map(({ span }) => span.id));
  return page.spans.flatMap((span) => {
    const inSpan = matches.get(span.id);
    if (inSpan === undefined || (page.layout.lines.get(span.id)?.id ?? span.id) === page.typingIn) return [];
    const reading = readings.get(span.id);
    const text = reading?.text ?? span.text;
    const font = fontOf(page.fonts, span);
    const glyphs = font?.glyphs;
    const run = runDrawnAt(page, { span, readings });
    const scale = run?.scale ?? settingOf(span, { font, text, asked: reading?.strategy, rules: page.rules });
    const fit = reading?.replaced && !previewed.has(span.id) && run === undefined ? page.fits[span.id] : undefined;
    const placed = run?.moved ?? span;
    const widePt = drawnWidthOf(placed, { text, glyphs, scale, fit });
    return matchesAcross(placed, { text, matches: inSpan, glyphs, widePt }).map(({ x0, x1 }) => ({ span, x0, x1 }));
  });
}

function runDrawnAt(
  page: Pick<PageState, "fonts" | "rules" | "layout">,
  { span, readings }: { span: SpanInfo; readings: ReadonlyMap<string, SpanReading> },
): DrawnRun | undefined {
  const lineEdit = readings.get(span.id)?.lineEdit;
  const line = page.layout.lines.get(span.id);
  if (lineEdit === undefined || line === undefined) return undefined;
  const texts = lineEdit.runs.map((run) => run.text);
  const drawn = lineDrawnAs(line, { texts, fonts: page.fonts, asked: lineEdit.strategy, rules: page.rules });
  return drawn?.find((run) => run.span.id === span.id);
}

/** The find bar's marks found again in `reading`; the place gone to stays while it's still found. */
function refound(state: Pick<EditorState, "find" | "layout">, reading: Reading): FindState {
  const { find, layout } = state;
  // The span typed in shows no marks (`tintsOn`), so its saved words do here.
  const found = foundIn(layout, { reading, find, typed: null });
  const current = found.some(({ id }) => id === find.current) ? find.current : null;
  return { ...find, matches: matchesOf(found, find.matches), current };
}

function placesIn(state: EditorState): string[] {
  return foundNow(state).map(({ id }) => id);
}

export function placeAt(state: EditorState): number | null {
  const index = state.find.current === null ? -1 : placesIn(state).indexOf(state.find.current);
  return index < 0 ? null : index + 1;
}

function placeAfter(places: readonly string[], current: string | null, step: 1 | -1): string | undefined {
  const index = current === null ? -1 : places.indexOf(current);
  if (index < 0) return step === 1 ? places[0] : places.at(-1);
  return places[(index + step + places.length) % places.length];
}

export function stepFind(editor: Editor, step: 1 | -1): void {
  const state = editor.store.get();
  if (!state.find.open) return;
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

function caughtUp(find: FindState): FindState {
  return { ...find, searched: find.query, judgedReplacement: find.replacement };
}

/** The find bar once the history changes: found again while it's open, and what Change all did no longer shown. */
export function findAfterChange(state: Pick<EditorState, "find" | "layout">, reading: Reading): FindState {
  const found = state.find.open ? refound(state, reading) : state.find;
  return found.changed === null ? found : { ...found, changed: null };
}

export interface FindCount {
  readonly places: number;
  readonly mismatches: number;
  readonly leftAsTheyAre: number;
}

function wouldEmpty({ text, matches }: Found, replacement: string): boolean {
  return replacement !== "" && isEmpty(replacedIn(text, matches, replacement));
}

interface Judging {
  readonly drawn: Drawn;
  readonly fonts: FontsByObject;
  readonly replacement: string;
  readonly rules: FitRules;
}

function looksDifferent(found: Found, judging: Judging): boolean {
  const replaced = judging.replacement === "" ? null : runsReplacedIn(found, judging.replacement);
  return runMatchesOf(found.runs, found.matches).some(([run, inRun], index) => {
    if (inRun.length === 0) return false;
    const judged = replaced === null ? run : { ...run, text: replaced[index] ?? run.text, strategy: undefined };
    return runLooksDifferent(judged, judging);
  });
}

function runLooksDifferent({ span, text, strategy }: FoundRun, { drawn, fonts, rules }: Judging): boolean {
  const font = fontOf(fonts, span);
  if (font === undefined) return span.fidelity !== "exact";
  if (editVerdictOf(drawn, { span, font, text, strategy }).fidelity !== "exact") return true;
  const fit = fitOf(span, { font, text, rules });
  return runsPast(fit.deltaPt, fit.roomPt, rules) && wayDrawn(strategy, fit) === "as-is";
}

export function findCount(state: EditorState): FindCount {
  const { find, doc, layout, drawn } = state;
  const found = foundNow(state);
  const changing = found.filter((each) => !wouldEmpty(each, find.judgedReplacement));
  const judging = { drawn, fonts: layout.fonts, replacement: find.judgedReplacement, rules: doc.fit };
  const mismatches = changing.filter((each) => looksDifferent(each, judging)).length;
  return { places: found.length, mismatches, leftAsTheyAre: found.length - changing.length };
}

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
  if (plain === "" || warnings.includes("")) return { plain: "", warned: "" };
  return { plain, warned: warnings.join(sentences.join_parts) };
}

export function countSentence(count: FindCount, sentences: Sentences): string {
  const { plain, warned } = countShown(count, sentences);
  return `${plain}${warned}`;
}

export function changedSentence(changed: number, sentences: Sentences): string {
  return fillCounted(sentences.changed_places, changed);
}

export function openFind(editor: Editor): void {
  finish(editor, true, {
    also: (next) => {
      const find = refound(next, next.reading);
      return { find: { ...find, open: true, opened: find.opened + 1 } };
    },
  });
}

export function closeFind(editor: Editor, { fromInside = false }: { fromInside?: boolean } = {}): void {
  clearTimeout(editor.finding.timer);
  const { find } = editor.store.get();
  const closed = find.closed + (fromInside ? 1 : 0);
  const ended = { open: false, matches: new Map(), current: null, closed, changedAll: 0 };
  editor.store.set({ find: { ...caughtUp(find), ...ended } });
}

export function typeQuery(editor: Editor, query: string): void {
  const { find } = editor.store.get();
  editor.store.set({ find: { ...find, query, changed: null } });
  clearTimeout(editor.finding.timer);
  editor.finding.timer = setTimeout(() => search(editor), FIND_DELAY_MS);
}

export function typeReplacement(editor: Editor, replacement: string): void {
  const { find } = editor.store.get();
  editor.store.set({ find: { ...find, replacement, changed: null } });
  clearTimeout(editor.finding.timer);
  editor.finding.timer = setTimeout(() => countReplacement(editor), FIND_DELAY_MS);
}

function countReplacement(editor: Editor): void {
  editor.store.set(hearing(caughtUpCount(editor.store.get())));
}

interface Counted {
  readonly find: FindState;
  readonly heard: string;
}

function caughtUpCount(state: EditorState): Counted {
  const { find } = state;
  if (find.searched !== find.query) return searched(state);
  if (find.judgedReplacement !== find.replacement) return counted(state, caughtUp(find));
  return { find, heard: "" };
}

function counted(state: EditorState, find: FindState): Counted {
  const sentence = countSentence(findCount({ ...state, find }), state.doc.sentences);
  if (find.searched === "" || sentence === find.announcedCount) return { find, heard: "" };
  return { find: { ...find, announcedCount: sentence }, heard: sentence };
}

function hearing({ find, heard }: Counted): Pick<EditorState, "find"> & Partial<EditorState> {
  return heard === "" ? { find } : { find, announcement: announcing(heard) };
}

export function setMatchCase(editor: Editor, matchCase: boolean): void {
  search(editor, { matchCase });
}

export function setWholeWords(editor: Editor, wholeWords: boolean): void {
  search(editor, { wholeWords });
}

function search(editor: Editor, asked: Partial<FindOptions> = {}): void {
  editor.store.set(hearing(searched(editor.store.get(), asked)));
}

function searched(state: EditorState, asked: Partial<FindOptions> = {}): Counted {
  const searching = { ...caughtUp({ ...state.find, ...asked }), changed: null, current: null };
  const find = refound({ ...state, find: searching }, state.reading);
  if (find.searched === "") return { find, heard: "" };
  const heard = countSentence(findCount({ ...state, find }), state.doc.sentences);
  return { find: { ...find, announcedCount: heard }, heard };
}

function changeAllEditsOf(
  layout: Pick<Layout, "lines">,
  { reading, replaced }: { reading: Reading; replaced: ReadonlyMap<string, string> },
): (Replace | ReplaceRuns)[] {
  const done = new Set<string>();
  return [...replaced].flatMap(([spanId, text]): (Replace | ReplaceRuns)[] => {
    const line = layout.lines.get(spanId);
    const othersChanged = line?.spans.some((span) => span.id !== spanId && replaced.has(span.id)) ?? false;
    const edited = reading.spans.get(spanId)?.lineEdit !== undefined;
    if (line === undefined || !(edited || othersChanged)) {
      return [{ kind: "replace", span_id: spanId, text }];
    }
    if (done.has(line.id)) return [];
    done.add(line.id);
    const runs = line.spans.map((span) => ({
      span_id: span.id,
      text: replaced.get(span.id) ?? textIn(reading, span),
    }));
    return [{ kind: "replace_runs", runs }];
  });
}

export function changeAll(editor: Editor): void {
  const state = editor.store.get();
  const { typed, ended } = typingEnd(state, true);
  const find = { ...caughtUp(state.find), changedAll: state.find.changedAll + 1 };
  // Emptying a span is redaction's job, so nothing to change to changes nothing.
  const found = find.replacement === "" ? [] : foundNow({ ...state, find });
  const changing = found.filter((each) => !wouldEmpty(each, find.replacement));
  clearTimeout(editor.finding.timer);
  if (changing.length === 0) {
    change(editor, typed, (next) => ({
      ...ended,
      ...hearing(counted(next, refound({ ...next, find }, next.reading))),
    }));
    return;
  }
  const replaced = new Map(
    changing.flatMap((each) => {
      const texts = runsReplacedIn(each, find.replacement);
      return each.runs.flatMap(({ span, text }, index) => {
        const now = texts[index] ?? text;
        return now === text ? [] : [[span.id, now] as const];
      });
    }),
  );
  const after = historyReducer(state.history, typed);
  const kept = readingOf(state.doc, { base: unsplitOf(state.layout), entries: entriesOf(after) }, state);
  const edits = changeAllEditsOf(kept.layout, { reading: kept.reading, replaced }).map((each) =>
    asWired(kept.layout, each),
  );
  change(editor, [typed, { kind: "add", edits }], (next) => ({
    ...ended,
    announcement: announcing(changedSentence(changing.length, state.doc.sentences)),
    find: { ...refound({ ...next, find }, next.reading), changed: changing.length, announcedCount: "" },
  }));
}
