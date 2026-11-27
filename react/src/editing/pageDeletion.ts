import type { Document } from "../api/types";
import { announcing, change, type Editor } from "./editor";
import { deleting } from "./history";
import { shownNumberOf } from "./project";
import { typingEnd } from "./typing";
import { fill, sentenceOrNothing } from "./words";

export function leftOutSentence({ sentences }: Pick<Document, "sentences">): string {
  return sentenceOrNothing(sentences.page_left_out);
}

export function putBackSentence(
  { sentences }: Pick<Document, "sentences">,
  { order, page }: { order: readonly number[]; page: number },
): string {
  return fill(sentences.page_put_back, { page: String(shownNumberOf(order, page)) });
}

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

export function putPageBack(editor: Editor, page: number): void {
  const { history } = editor.store.get();
  change(editor, { kind: "remove", ids: deleting(history, page) }, ({ reading, doc }) => ({
    announcement: announcing(putBackSentence(doc, { order: reading.order, page })),
    focusTo: { page, block: "nearest" },
  }));
}
