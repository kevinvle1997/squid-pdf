// How large the pages show: as wide as each is printed, or narrower to fit the window, until the user
// zooms to a share of the printed size. Zoomed past the window, the pages scroll sideways.
import { MAX_SCALE, PX_PER_PT, ZOOM_SKIP, ZOOM_STEPS } from "./constants";
import type { Editor, EditorState } from "./editor";
import { shownNumberOf } from "./project";

/** The share of its printed size asked for, null to fit the window; and the share the first page shows at. */
export interface Zoom {
  readonly asked: number | null;
  readonly shown: number;
}

/** Fitting the window, before the first page has said what share it shows at. */
export const FITTED: Zoom = { asked: null, shown: 1 };

/** The step in from `shown` (`step` 1) or out (-1), skipping one too close to tell; past either end, no further. */
export function zoomStep(shown: number, step: 1 | -1): number {
  const first = ZOOM_STEPS[0];
  const last = ZOOM_STEPS[ZOOM_STEPS.length - 1] ?? first;
  if (step === 1) return ZOOM_STEPS.find((share) => share > shown * ZOOM_SKIP) ?? Math.max(last, shown);
  return ZOOM_STEPS.findLast((share) => share < shown / ZOOM_SKIP) ?? Math.min(first, shown);
}

/** The scale page images are drawn at for `share` on a screen of `pixelRatio`: sharper as it zooms in, never blurrier than now. */
export function scaleFor(share: number, { pixelRatio, scale }: { pixelRatio: number; scale: number }): number {
  return Math.max(scale, Math.min(MAX_SCALE, Math.ceil(pixelRatio * PX_PER_PT * share)));
}

/** Zoom in or out a step from what shows, the page images drawn sharp enough for it. */
export function zoomBy(editor: Editor, step: 1 | -1, { pixelRatio }: { pixelRatio: number }): void {
  const { zoom, scale } = editor.store.get();
  const asked = zoomStep(zoom.shown, step);
  editor.store.set({ zoom: { asked, shown: asked }, scale: scaleFor(asked, { pixelRatio, scale }) });
}

/** Back to fitting the window: as wide as printed, or narrower where the window is. */
export function zoomToFit(editor: Editor): void {
  const { zoom } = editor.store.get();
  if (zoom.asked !== null) editor.store.set({ zoom: { ...zoom, asked: null } });
}

/** The first page measured the share it shows at, as the window or the zoom changed. */
export function zoomShown(editor: Editor, shown: number): void {
  const { zoom } = editor.store.get();
  // A share already shown, to the percent the control says: nothing to redraw.
  if (Math.round(shown * 100) !== Math.round(zoom.shown * 100)) editor.store.set({ zoom: { ...zoom, shown } });
}

/** The page most in view, as the pages say while the document scrolls: the one under the window's middle. */
export function pageInView(editor: Editor, page: number): void {
  if (editor.store.get().pageInView !== page) editor.store.set({ pageInView: page });
}

/** What the bar says of where the reader is, among the pages the download has: "Page 3 of 8", or "1 page". */
export function whereSentence({ reading, pageInView: page }: Pick<EditorState, "reading" | "pageInView">): string {
  const pages = reading.order.length;
  return pages === 1 ? "1 page" : `Page ${shownNumberOf(reading.order, page)} of ${pages}`;
}
