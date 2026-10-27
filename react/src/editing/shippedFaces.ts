// The faces we ship that new text offers past the page's own, as the server's font list says: which are handwriting.
// The list is the same for every document under one build, so it's asked for once per build.
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

// Each build's list, asked for once: a failed one is asked for again.
const lists = new Map<string, Promise<FontList>>();

/** The handwriting faces in `list`, by name. */
export function handwritingIn(list: FontList): string[] {
  return list.families
    .filter(({ category }) => category === "handwriting")
    .flatMap(({ faces }) => faces.map(({ name }) => name));
}

/** Ask for the font list of the document's build, unless it came already: what it says of handwriting goes in the editor. */
export async function loadShippedFaces(editor: Editor): Promise<void> {
  const { store } = editor;
  if (store.get().shippedFaces.handwriting.length > 0) return;
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
