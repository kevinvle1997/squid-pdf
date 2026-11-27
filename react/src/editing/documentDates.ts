import { learnDateLanguages } from "./dateNames";
import { dateReadingOf } from "./dates";
import type { Editor } from "./editor";

/** Not before: a month named in a language learnt later could be one the languages learnt so far settle wrongly. */
export function readDocumentDates(editor: Editor): void {
  if (editor.store.get().dates !== null) return;
  void learnDateLanguages().then(() => readNow(editor));
}

function readNow(editor: Editor): void {
  const { layout } = editor.store.get();
  const texts = [...layout.spans.values()].map((span) => span.text);
  const dates = dateReadingOf(texts, { readerLanguages: navigator.languages });
  editor.store.set({ dates });
}
