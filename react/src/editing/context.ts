// What every part of the open document reads: the document, the edits, and what to do.
import { createContext, useContext } from "react";
import type { Document, FitInfo, FontInfo } from "../api/types";
import type { Preview } from "./useStrips";

export interface Editing {
  spanId: string;
  atPt: number | null; // where in the span the press was, from its start: the word to select
}

export interface EditorState {
  doc: Document;
  scale: number;
  fonts: ReadonlyMap<string, FontInfo>;
  latest: ReadonlyMap<string, string>; // each replaced span's text now
  fits: Readonly<Record<string, FitInfo>>;
  previews: ReadonlyMap<string, Preview>;
  editing: Editing | null;
  returnedTo: string | null; // the span focus went back to after its edit: its note stays shut
  edit: (spanId: string, atPt: number | null) => void;
  commit: (spanId: string, text: string) => void;
  stopEditing: () => void;
  returnTo: (spanId: string | null) => void;
  revert: (spanId: string) => void;
  settled: (spanId: string) => void;
  say: (text: string) => void;
  imageFailed: () => void;
}

export const EditorContext = createContext<EditorState | null>(null);

export function useEditor(): EditorState {
  const state = useContext(EditorContext);
  if (state === null) throw new Error("useEditor is only for what the editor draws");
  return state;
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
  return { left: across(bbox.x0), top: down(bbox.y0), width: across(bbox.x1 - bbox.x0), height: down(bbox.y1 - bbox.y0) };
}

/** A length in points as CSS, against the page's width: the page layer is a size container. */
export function points(value: number, page: { width: number }): string {
  return `${(value / page.width) * 100}cqw`;
}
