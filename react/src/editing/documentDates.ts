// What settles how the document's dates read: worked out as its first edit starts, off the page's first
// draw, and again once every language Intl has dates for is learnt, so an open field reads its date anew.
import { learnEveryLanguage } from "./dateNames";
import { dateReadingOf } from "./dates";
import type { Editor } from "./editor";

/** Work out how the document's dates read, once, and learn the rest of the languages behind it. */
export function readDocumentDates(editor: Editor): void {
  if (editor.store.get().layout.dates !== null) return;
  readNow(editor);
  learnEveryLanguage(() => readNow(editor));
}

function readNow(editor: Editor): void {
  const { layout } = editor.store.get();
  const texts = [...layout.spans.values()].map((span) => span.text);
  const dates = dateReadingOf(texts, { readerLanguages: navigator.languages });
  editor.store.set({ layout: { ...layout, dates } });
}
