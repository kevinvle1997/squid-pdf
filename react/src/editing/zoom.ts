import { MAX_SCALE, PX_PER_PT, ZOOM_SKIP, ZOOM_STEPS } from "./constants";
import type { Editor, EditorState } from "./editor";
import { shownNumberOf } from "./project";

export interface Zoom {
  readonly asked: number | null;
  readonly shown: number;
}

export const FITTED: Zoom = { asked: null, shown: 1 };

export function zoomStep(shown: number, step: 1 | -1): number {
  const first = ZOOM_STEPS[0];
  const last = ZOOM_STEPS[ZOOM_STEPS.length - 1] ?? first;
  if (step === 1) return ZOOM_STEPS.find((share) => share > shown * ZOOM_SKIP) ?? Math.max(last, shown);
  return ZOOM_STEPS.findLast((share) => share < shown / ZOOM_SKIP) ?? Math.min(first, shown);
}

export function scaleFor(share: number, { pixelRatio, scale }: { pixelRatio: number; scale: number }): number {
  return Math.max(scale, Math.min(MAX_SCALE, Math.ceil(pixelRatio * PX_PER_PT * share)));
}

export function zoomBy(editor: Editor, step: 1 | -1, { pixelRatio }: { pixelRatio: number }): void {
  const { zoom, scale } = editor.store.get();
  const asked = zoomStep(zoom.shown, step);
  editor.store.set({ zoom: { asked, shown: asked }, scale: scaleFor(asked, { pixelRatio, scale }) });
}

export function zoomToFit(editor: Editor): void {
  const { zoom } = editor.store.get();
  if (zoom.asked !== null) editor.store.set({ zoom: { ...zoom, asked: null } });
}

export function zoomShown(editor: Editor, shown: number): void {
  const { zoom } = editor.store.get();
  if (Math.round(shown * 100) !== Math.round(zoom.shown * 100)) editor.store.set({ zoom: { ...zoom, shown } });
}

export function pageInView(editor: Editor, page: number): void {
  if (editor.store.get().pageInView !== page) editor.store.set({ pageInView: page });
}

export function whereSentence({ reading, pageInView: page }: Pick<EditorState, "reading" | "pageInView">): string {
  const pages = reading.order.length;
  return pages === 1 ? "1 page" : `Page ${shownNumberOf(reading.order, page)} of ${pages}`;
}
