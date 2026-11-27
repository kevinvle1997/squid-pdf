// The list is the same for every document under one build, so it's asked for once per build.
import { fontList } from "../api/client";
import type { FontList } from "../api/types";
import { sentenceOf } from "../bugs";
import type { Editor } from "./editor";

export interface ShippedFaces {
  readonly handwriting: readonly string[];
  readonly failed: string | null;
}

export const NO_SHIPPED_FACES: ShippedFaces = { handwriting: [], failed: null };

const lists = new Map<string, Promise<FontList>>();

export function handwritingIn(list: FontList): string[] {
  return list.families
    .filter(({ category }) => category === "handwriting")
    .flatMap(({ faces }) => faces.map(({ name }) => name));
}

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
