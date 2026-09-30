// The edited document, downloaded under its own name. An edit still being typed goes in first.
import { exportPdf, ProblemError } from "../api/client";
import { reportBug } from "../bugs";
import type { Editor } from "./editor";
import { plain, warn } from "./notices";
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
  const { store, reopener, file } = editor;
  if (store.get().exporting) return;
  store.set({ exporting: true });
  finish(editor, true);
  const { edits } = store.get().reading;
  try {
    const exported = await reopener.withDocument((doc) => exportPdf(doc.id, [...edits]));
    download(exported.pdf, file.name);
    const leftOut = exported.skipped.length > 0;
    const text = leftOut
      ? store.get().doc.copy.export_left_out
      : (exported.notices[0]?.detail ?? `Downloaded ${file.name}.`);
    const notice = leftOut ? warn(text) : plain(text);
    store.set({ notices: { ...store.get().notices, export: notice }, said: text });
  } catch (error) {
    const text = error instanceof ProblemError ? error.problem.detail : reportBug(error);
    store.set({ notices: { ...store.get().notices, export: warn(text) }, said: text });
  } finally {
    store.set({ exporting: false });
  }
}
