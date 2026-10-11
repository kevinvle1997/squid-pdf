// The edited document, downloaded under its own name, or split in two at a page. An edit still
// being typed goes in first.
import { exportPdf, ProblemError } from "../api/client";
import type { Edit } from "../api/types";
import { reportBug } from "../bugs";
import { type Editor, spoken } from "./editor";
import { type Notice, plain, warn } from "./notices";
import { finish } from "./typing";

/** One file to download: which pages, in order, or every page; and its name. */
interface Part {
  readonly pages: readonly number[] | undefined;
  readonly name: string;
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
  await exported(editor, [{ pages: undefined, name: editor.file.name }]);
}

/** Download the document as two files: its pages up to `page`, and the rest. Every edit goes to both. */
export async function splitAfter(editor: Editor, page: number): Promise<void> {
  const count = editor.store.get().doc.pages.length;
  const [before, after] = splitPages(count, page);
  await exported(
    editor,
    [before, after].map((pages) => ({ pages, name: partName(editor.file.name, pages) })),
  );
}

/** The pages either side of a split after `page`, of `count`. */
export function splitPages(count: number, page: number): [number[], number[]] {
  const all = Array.from({ length: count }, (_, index) => index);
  return [all.slice(0, page + 1), all.slice(page + 1)];
}

/** A part's file name: the upload's, with the pages it holds, numbered from one as a reader counts them. */
export function partName(fileName: string, pages: readonly number[]): string {
  const stem = fileName.replace(/\.pdf$/i, "");
  const first = (pages[0] ?? 0) + 1;
  const last = (pages[pages.length - 1] ?? 0) + 1;
  return first === last ? `${stem}, page ${first}.pdf` : `${stem}, pages ${first}–${last}.pdf`;
}

/** Download each part in turn, stopping at the first that fails, and say everything once. */
async function exported(editor: Editor, parts: readonly Part[]): Promise<void> {
  const { store } = editor;
  if (store.get().exporting) return;
  finish(editor, true);
  store.set({ exporting: true });
  const edits = store.get().reading.edits;
  const said: Notice[] = [];
  let went = true;
  for (const part of parts) {
    const result = await downloaded(editor, edits, part);
    said.push(...result.said);
    went = result.went;
    if (!went) break;
  }
  // Two parts can say the same: an edit left out of both, say.
  const once = [...new Map(said.map((notice) => [notice.text, notice])).values()];
  store.set({
    exporting: false,
    ...(went && { exported: edits }),
    notices: { ...store.get().notices, export: once },
    said: spoken(once.map((notice) => notice.text).join(" ")),
  });
}

/** Export `edits` and download `part`; everything to say of it, warned where it isn't as asked, and whether it went. */
async function downloaded(
  editor: Editor,
  edits: readonly Edit[],
  { pages, name }: Part,
): Promise<{ said: Notice[]; went: boolean }> {
  const { store, reopener } = editor;
  try {
    const file = await reopener.withDocument((doc) => exportPdf(doc.id, [...edits], { pages }));
    download(file.pdf, name);
    // What saving did to the whole file other than asked, each said: a split dropping its tags, say.
    const otherwise = file.notices.map((notice) => warn(notice.detail));
    const leftOut = file.skipped.length > 0 ? [warn(store.get().doc.copy.export_left_out)] : [];
    const said = [...leftOut, ...otherwise];
    return { said: said.length > 0 ? said : [plain(`Downloaded ${name}.`)], went: true };
  } catch (error) {
    return { said: [warn(error instanceof ProblemError ? error.problem.detail : reportBug(error))], went: false };
  }
}
