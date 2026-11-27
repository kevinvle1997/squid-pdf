import type { Editor, FocusTo } from "./editor";
import type { Reading } from "./project";

export function goToPage(editor: Editor, page: number): void {
  editor.store.set({ focusTo: { page, block: "start" } });
}

export function pageControlOf(reading: Pick<Reading, "order">, page: number): FocusTo {
  // The only page left can't be deleted: its button can't take focus.
  return reading.order.length > 1 ? { page, block: "nearest" } : { tick: page };
}
