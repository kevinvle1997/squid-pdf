// The edited document, downloaded under its own name. An edit still being typed goes in first.
import { exportPdf, ProblemError } from "../api/client";
import type { Edit } from "../api/types";
import { reportBug } from "../bugs";
import { type Editor, spoken } from "./editor";
import { type Notice, plain, warn } from "./notices";
import { finish } from "./typing";

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
  const { store } = editor;
  if (store.get().exporting) return;
  finish(editor, true);
  store.set({ exporting: true });
  const edits = store.get().reading.edits;
  const { said, went } = await downloaded(editor, edits);
  store.set({
    exporting: false,
    ...(went && { exported: edits }),
    notices: { ...store.get().notices, export: said },
    said: spoken(said.map((notice) => notice.text).join(" ")),
  });
}

/** Export `edits` and download the file; everything to say of it, warned where it isn't as asked, and whether it went. */
async function downloaded(editor: Editor, edits: readonly Edit[]): Promise<{ said: Notice[]; went: boolean }> {
  const { store, reopener, file } = editor;
  try {
    const exported = await reopener.withDocument((doc) => exportPdf(doc.id, [...edits]));
    download(exported.pdf, file.name);
    // What saving did to the whole file other than asked, each said: a split dropping its tags, say.
    const otherwise = exported.notices.map((notice) => warn(notice.detail));
    const leftOut = exported.skipped.length > 0 ? [warn(store.get().doc.copy.export_left_out)] : [];
    const said = [...leftOut, ...otherwise];
    return { said: said.length > 0 ? said : [plain(`Downloaded ${file.name}.`)], went: true };
  } catch (error) {
    return { said: [warn(error instanceof ProblemError ? error.problem.detail : reportBug(error))], went: false };
  }
}
