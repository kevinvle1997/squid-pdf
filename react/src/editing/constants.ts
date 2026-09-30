// The editing feature's tuning. Thresholds the server owns come in Document.fit, never from here.

/** CSS pixels per point: a page shows at its printed size on a 96 dpi screen. */
export const PX_PER_PT = 4 / 3;

/** The page image scales the API draws at, in pixels per point. */
export const MIN_SCALE = 1;
export const MAX_SCALE = 4;

/** A strip reaches this far past a span's box, so accents and underlines come with it. */
export const STRIP_PAD_PT = 2;

/** A new line has no box until it's drawn: an em above its baseline and this far below covers any face we ship. */
export const INSERT_ASCENT_EM = 1;
export const INSERT_DESCENT_EM = 0.3;

/** A second press on the same span within this is a double press: it edits. */
export const DOUBLE_PRESS_MS = 400;

/** How long a pointer rests on a span before its note shows. Focus shows it at once. */
export const NOTE_DELAY_MS = 250;

/** Page images load when they come this close to the viewport. */
export const LAZY_MARGIN = "100% 0px";

/** A space the font has no width for is counted at this share of the size, as TeX sets it. */
export const MISSING_SPACE_EM = 0.25;
