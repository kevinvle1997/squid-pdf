// The faces we ship that new text offers past the page's own, as the server's font list says: which are handwriting.
// The same under one build, it's asked for once a build, after a page's image: not to share the link with the first.
import { fontList } from "../api/client";
import type { FontList } from "../api/types";
import { sentenceOf } from "../bugs";
import type { Editor } from "./editor";

/** The handwriting faces we ship, by name, in the list's order; or why the list didn't come. */
export interface ShippedFaces {
  readonly handwriting: readonly string[];
  readonly failed: string | null; // in words for the user
}

/** Before the list comes. */
export const NO_SHIPPED_FACES: ShippedFaces = { handwriting: [], failed: null };

// Each build's list, asked for once: a failed one is asked for again, by the next editor.
const lists = new Map<string, Promise<FontList>>();

/** The handwriting faces in `list`, by name. */
export function handwritingIn(list: FontList): string[] {
  return list.families
    .filter(({ category }) => category === "handwriting")
    .flatMap(({ faces }) => faces.map(({ name }) => name));
}

/** Ask for the font list of the document's build, once an editor: what it says of handwriting goes in the editor, or why it didn't come. */
export function loadShippedFaces(editor: Editor): Promise<void> {
  editor.fontList.asked ??= listed(editor);
  return editor.fontList.asked;
}

/** The font list of the document's build in the editor, or why it didn't come. */
async function listed(editor: Editor): Promise<void> {
  const { store } = editor;
  const { build } = store.get().doc;
  const asking = lists.get(build) ?? fontList(build);
  lists.set(build, asking);
  try {
    store.set({ shippedFaces: { handwriting: handwritingIn(await asking), failed: null } });
  } catch (error) {
    lists.delete(build);
    store.set({ shippedFaces: { handwriting: [], failed: sentenceOf(error) } });
  }
}
