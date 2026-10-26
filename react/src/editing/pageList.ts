// The page list: a tick for each page the download has, going to it. Navigation only: it changes nothing.
import type { Editor } from "./editor";

/** Go to page `page`: brought into view from its top, focus on its delete button, its first control. */
export function goToPage(editor: Editor, page: number): void {
  editor.store.set({ focusTo: { page, block: "start" } });
}
