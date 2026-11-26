// How sharp page images are drawn: points to CSS pixels, the scales the API draws at, and this screen's. Imports
// nothing, so the app's shell asks for the first pages at the scale the editor draws them.

/** CSS pixels per point: a page shows at its printed size on a 96 dpi screen. */
export const PX_PER_PT = 4 / 3;

/** The page image scales the API draws at, in pixels per point. */
const MIN_SCALE = 1;
export const MAX_SCALE = 4;

/** The scale page images are drawn at on this screen: sharp at the printed size, and no larger than the API draws. */
export function screenScale(): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.ceil(window.devicePixelRatio * PX_PER_PT)));
}
