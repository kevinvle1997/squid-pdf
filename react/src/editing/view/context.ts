import { createContext, useContext } from "react";
import { useSyncExternalStoreWithSelector } from "use-sync-external-store/with-selector";
import type { Editor, EditorState } from "../editor";

export const EditorContext = createContext<Editor | null>(null);

export const SlipLayerContext = createContext<HTMLElement | null>(null);

export function useEditor(): Editor {
  const editor = useContext(EditorContext);
  if (editor === null) throw new Error("useEditor is only for what the editor draws");
  return editor;
}

/** A selection built fresh each time, an object of several parts, passes `shallowEqual` as `isEqual`, or every change would look like one. */
export function useEditorState<T>(select: (state: EditorState) => T, isEqual?: (a: T, b: T) => boolean): T {
  const { store } = useEditor();
  return useSyncExternalStoreWithSelector(store.subscribe, store.get, undefined, select, isEqual);
}
