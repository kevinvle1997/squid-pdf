// What settles how the document's dates read: worked out once every language Intl has dates for is learnt,
// in the background as the first edit starts, so the field opens at once and reads its date once they are.
import { learnEveryLanguage } from "./dateNames";
import { dateReadingOf } from "./dates";
import type { Editor } from "./editor";

/**
 * Work out how the document's dates read, once every language is learnt. Why not before: a month named in a
 * language learnt later could be one the languages learnt so far settle wrongly.
 */
export function readDocumentDates(editor: Editor): void {
  if (editor.store.get().layout.dates !== null) return;
  void learnEveryLanguage().then(() => readNow(editor));
}

function readNow(editor: Editor): void {
  const { layout } = editor.store.get();
  const texts = [...layout.spans.values()].map((span) => span.text);
  const dates = dateReadingOf(texts, { readerLanguages: navigator.languages });
  editor.store.set({ layout: { ...layout, dates } });
}
