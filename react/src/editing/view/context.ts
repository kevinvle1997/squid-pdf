// The open document's editor, for what draws it. State is read through a selector, so a
// component redraws only when what it selected changes; actions take the editor itself,
// which never changes, so passing it down redraws nothing.
import { createContext, useContext } from "react";
import { useSyncExternalStoreWithSelector } from "use-sync-external-store/with-selector";
import type { Editor, EditorState } from "../editor";

export const EditorContext = createContext<Editor | null>(null);

/** The editor, for a handler to act on. */
export function useEditor(): Editor {
  const editor = useContext(EditorContext);
  if (editor === null) throw new Error("useEditor is only for what the editor draws");
  return editor;
}

/**
 * Part of the editor's state: what's drawn from it redraws only when it changes. That's by
 * `Object.is`, unless `isEqual` says otherwise: a selection built fresh each time, as an
 * object of several parts is, passes `shallowEqual`, or every change would look like one.
 */
export function useEditorState<T>(select: (state: EditorState) => T, isEqual?: (a: T, b: T) => boolean): T {
  const { store } = useEditor();
  return useSyncExternalStoreWithSelector(store.subscribe, store.get, undefined, select, isEqual);
}

/** Put focus on a span's mark, as a keyboard user expects after editing it. */
export function focusSpan(spanId: string): void {
  document.getElementById(markId(spanId))?.focus();
}

export function markId(spanId: string): string {
  return `span-${spanId}`;
}
