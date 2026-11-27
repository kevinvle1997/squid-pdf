// The whole export starts on the way to pressing Export, and the press takes it if the edits it was asked with still stand.
import { type Exported, exportPdf } from "../api/client";
import type { Edit } from "../api/types";
import { sentenceOf } from "../bugs";
import { EXPORT_INTENT_MS } from "./constants";
import { announcing, type Editor, type EditorState } from "./editor";
import { heardIn, type Notice, plain, warn } from "./notices";
import { textIn } from "./project";
import { type Draft, finish } from "./typing";
import { fill } from "./words";

export interface ExportAhead {
  timer?: ReturnType<typeof setTimeout>;
  started?: {
    readonly edits: readonly Edit[];
    readonly ask: AbortController;
    readonly file: Promise<Exported>;
    answered: boolean;
  };
}

export function unexported(state: EditorState): boolean {
  const { draft, layout, exported, reading } = state;
  const reads = draft === null ? undefined : readsNow(draft, { layout, reading });
  // Closing the tab ends no typing: what's in the field never reaches the history.
  if (draft !== null && draft.text !== reads) return true;
  const { edits } = reading;
  return edits.length !== exported.length || edits.some((edit, index) => edit !== exported[index]);
}

function readsNow(draft: Draft, { layout, reading }: Pick<EditorState, "layout" | "reading">): string | undefined {
  if (draft.kind === "newText") return draft.of?.edit.text ?? "";
  const span = layout.spans.get(draft.spanId);
  return span === undefined ? undefined : textIn(reading, span);
}

export function exportIntended(editor: Editor): void {
  clearTimeout(editor.ahead.timer);
  editor.ahead.timer = setTimeout(() => exportAhead(editor), EXPORT_INTENT_MS);
}

export function exportUnintended(editor: Editor): void {
  const { ahead } = editor;
  clearTimeout(ahead.timer);
  if (ahead.started?.answered !== false) return;
  ahead.started.ask.abort();
  ahead.started = undefined;
}

export function exportAhead(editor: Editor): void {
  const { store, reopener, ahead } = editor;
  clearTimeout(ahead.timer);
  const { exporting, draft, reading } = store.get();
  const ready = !exporting && draft === null && reading.edits.length > 0;
  if (!ready || ahead.started?.edits === reading.edits) return;
  ahead.started?.ask.abort();
  const { edits } = reading;
  const ask = new AbortController();
  const file = reopener.withDocument((doc) => exportPdf(doc.id, [...edits], { signal: ask.signal }));
  const started = { edits, ask, file, answered: false };
  file.then(
    () => {
      started.answered = true;
    },
    () => undefined,
  );
  ahead.started = started;
}

function takenAhead(editor: Editor, edits: readonly Edit[], pages: Part["pages"]): Promise<Exported> | undefined {
  const { ahead } = editor;
  clearTimeout(ahead.timer);
  const started = ahead.started;
  ahead.started = undefined;
  if (started === undefined) return undefined;
  if (started.edits === edits && pages === undefined) return started.file;
  started.ask.abort();
  return undefined;
}

interface Part {
  readonly pages: readonly number[] | undefined;
  readonly name: (edited: boolean) => string;
}

function download(pdf: Blob, name: string): void {
  const url = URL.createObjectURL(pdf);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  // Not at once: some browsers are still reading the file when click() returns.
  setTimeout(() => URL.revokeObjectURL(url));
}

export async function exportNow(editor: Editor): Promise<void> {
  await exportParts(editor, [{ pages: undefined, name: (edited) => exportName(editor.file.name, { edited }) }]);
}

/** Marked edited when it is, so it isn't taken for the original in a downloads folder. */
export function exportName(fileName: string, { edited }: { edited: boolean }): string {
  if (!edited) return fileName;
  return `${fileName.replace(/\.pdf$/i, "")}, edited.pdf`;
}

export async function splitAfter(editor: Editor, page: number): Promise<void> {
  const [before, after] = splitPages(editor.store.get().reading.order, page);
  await exportParts(
    editor,
    [before, after].map((pages) => ({ pages, name: () => partName(editor.file.name, pages) })),
  );
}

export function splitPages(order: readonly number[], page: number): [number[], number[]] {
  const after = order.indexOf(page) + 1;
  return [order.slice(0, after), order.slice(after)];
}

export function partName(fileName: string, pages: readonly number[]): string {
  const stem = fileName.replace(/\.pdf$/i, "");
  const first = (pages[0] ?? 0) + 1;
  const last = (pages[pages.length - 1] ?? 0) + 1;
  return first === last ? `${stem}, page ${first}.pdf` : `${stem}, pages ${first}–${last}.pdf`;
}

async function exportParts(editor: Editor, parts: readonly Part[]): Promise<void> {
  const { store } = editor;
  if (store.get().exporting) return;
  finish(editor, true, { also: { exporting: true } });
  const edits = store.get().reading.edits;
  const notices: Notice[] = [];
  let went = true;
  for (const part of parts) {
    const result = await downloaded(editor, edits, { pages: part.pages, name: part.name(edits.length > 0) });
    notices.push(...result.notices);
    went = result.went;
    if (!went) break;
  }
  const once = [...new Map(notices.map((notice) => [notice.text, notice])).values()];
  store.set({
    exporting: false,
    ...(went && { exported: edits }),
    notices: { ...store.get().notices, export: once },
    announcement: announcing(heardIn(once)),
  });
}

async function downloaded(
  editor: Editor,
  edits: readonly Edit[],
  { pages, name }: { pages: Part["pages"]; name: string },
): Promise<{ notices: Notice[]; went: boolean }> {
  const { store, reopener } = editor;
  const asked = () => reopener.withDocument((doc) => exportPdf(doc.id, [...edits], { pages }));
  // One started ahead that failed is asked for again: the network may be back.
  const ahead = takenAhead(editor, edits, pages)?.catch(asked);
  try {
    const file = await (ahead ?? asked());
    download(file.pdf, name);
    const otherwise = file.notices.map((notice) => warn(notice.detail));
    const { sentences } = store.get().doc;
    const leftOut = file.skipped.length > 0 ? [warn(sentences.export_left_out)] : [];
    const notices = [...leftOut, ...otherwise];
    const sentence = fill(sentences.downloaded, { name });
    const downloadedNotices = sentence === "" ? [] : [plain(sentence)];
    return { notices: notices.length > 0 ? notices : downloadedNotices, went: true };
  } catch (error) {
    return { notices: [warn(sentenceOf(error))], went: false };
  }
}
