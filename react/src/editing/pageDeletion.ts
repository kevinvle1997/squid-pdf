// Leaving a page out of the download, and putting it back: each one edit in the history, undone as any edit is.
// The edit names the page by its number in the original; what's said of it, by its number as shown.
import type { Document } from "../api/types";
import { announcing, change, type Editor } from "./editor";
import { deleting } from "./history";
import { shownNumberOf } from "./project";
import { typingEnd } from "./typing";
import { fill, sentenceOrNothing } from "./words";

/** What's said of a page left out of the download, on its slip and as it goes. */
export function leftOutSentence({ sentences }: Pick<Document, "sentences">): string {
  return sentenceOrNothing(sentences.page_left_out);
}

/** What's said of page `page` back in the download: its number among the pages shown, `order`, now it's back. */
export function putBackSentence(
  { sentences }: Pick<Document, "sentences">,
  { order, page }: { order: readonly number[]; page: number },
): string {
  return fill(sentences.page_put_back, { page: String(shownNumberOf(order, page)) });
}

/** Leave page `page` out of the download, as one step; words being typed are kept first, as their own. Focus goes to its slip's Put back. */
export function deletePage(editor: Editor, page: number): void {
  const state = editor.store.get();
  const { reading, doc } = state;
  // A download needs a page, and a page left out has no delete of its own.
  if (reading.order.length <= 1 || reading.deleted.has(page)) return;
  const { typed, ended } = typingEnd(state, true);
  change(editor, [typed, { kind: "add", edits: [{ kind: "delete_page", page }] }], {
    ...ended,
    announcement: announcing(leftOutSentence(doc)),
    focusTo: { page, block: "nearest" },
  });
}

/** Put page `page` back in the download, its edits with it; focus goes to its delete button. */
export function putPageBack(editor: Editor, page: number): void {
  const { history } = editor.store.get();
  change(editor, { kind: "remove", ids: deleting(history, page) }, ({ reading, doc }) => ({
    announcement: announcing(putBackSentence(doc, { order: reading.order, page })),
    focusTo: { page, block: "nearest" },
  }));
}
