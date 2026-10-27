// The page list: a tick for each page the download has, going to it. Navigation only: it changes nothing.
import type { Editor, FocusTo } from "./editor";
import type { Reading } from "./project";

/** Go to page `page`: brought into view from its top, focus on its delete button, its first control. */
export function goToPage(editor: Editor, page: number): void {
  editor.store.set({ focusTo: { page, block: "start" } });
}

/** The control on page `page` focus goes to when what had it there goes: its delete button, or the only page's tick. */
export function pageControlOf(reading: Pick<Reading, "order">, page: number): FocusTo {
  // The only page left can't be deleted: its button can't take focus.
  return reading.order.length > 1 ? { page, block: "nearest" } : { tick: page };
}
