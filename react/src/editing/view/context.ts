// The open document's editor, for what draws it. State is read through a selector, so a
// component redraws only when what it selected changes; actions take the editor itself,
// which never changes, so passing it down redraws nothing.
import { createContext, useContext, useSyncExternalStore } from "react";
import type { Editor, EditorState } from "../editor";

export const EditorContext = createContext<Editor | null>(null);

/** The editor, for a handler to act on. */
export function useEditor(): Editor {
  const editor = useContext(EditorContext);
  if (editor === null) throw new Error("useEditor is only for what the editor draws");
  return editor;
}

/**
 * Part of the editor's state. `select` returns something the state holds, or a string or a
 * number: a new object from each call would look like a change every time.
 */
export function useEditorState<T>(select: (state: EditorState) => T): T {
  const { store } = useEditor();
  return useSyncExternalStore(store.subscribe, () => select(store.get()));
}

/** Put focus on a span's mark, as a keyboard user expects after editing it. */
export function focusSpan(spanId: string): void {
  document.getElementById(markId(spanId))?.focus();
}

export function markId(spanId: string): string {
  return `span-${spanId}`;
}

/** A box on the page as percentages of it, so it scales with the page. */
export function boxOf(
  bbox: { x0: number; y0: number; x1: number; y1: number },
  page: { width: number; height: number },
): { left: string; top: string; width: string; height: string } {
  const across = (value: number) => `${(value / page.width) * 100}%`;
  const down = (value: number) => `${(value / page.height) * 100}%`;
  return {
    left: across(bbox.x0),
    top: down(bbox.y0),
    width: across(bbox.x1 - bbox.x0),
    height: down(bbox.y1 - bbox.y0),
  };
}

/** A length in points as CSS, against the page's width: the page layer is a size container. */
export function points(value: number, page: { width: number }): string {
  return `${(value / page.width) * 100}cqw`;
}
