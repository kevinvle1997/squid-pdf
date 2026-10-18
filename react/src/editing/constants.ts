// The editing feature's tuning. Thresholds the server owns come in Document.fit, never from here.

/** CSS pixels per point: a page shows at its printed size on a 96 dpi screen. */
export const PX_PER_PT = 4 / 3;

/** The page image scales the API draws at, in pixels per point. */
export const MIN_SCALE = 1;
export const MAX_SCALE = 4;

/** A strip reaches this far past a span's box, so accents and underlines come with it. */
export const STRIP_PAD_PT = 2;

/** A slip under a span, the field's chip or the redaction prompt, starts this far below it: clear of the mark under it. */
export const SLIP_GAP_PT = 4;

/** The edit field reaches this far past what's typed, a share of the size, so the caret has room after the last letter. */
export const FIELD_SPARE_EM = 0.5;

/** A new line has no box until it's drawn: an em above its baseline and this far below covers any face we ship. */
export const INSERT_ASCENT_EM = 1;
export const INSERT_DESCENT_EM = 0.3;

/** A second press on the same span within this is a double press: it edits. */
export const DOUBLE_PRESS_MS = 400;

/** How long a pointer rests on a span before its note shows. Focus shows it at once. */
export const NOTE_DELAY_MS = 250;

/** Find runs once typing in it stops for this long: soon enough to feel live, late enough to skip each letter. */
export const FIND_DELAY_MS = 200;

/**
 * How long the pointer rests on Export, or focus stays on it, before the export starts for the press to
 * take: longer than passing over it on the way elsewhere, shorter than the reach to press it.
 */
export const EXPORT_INTENT_MS = 150;

/** Page images load when they come this close to the viewport. */
export const LAZY_MARGIN = "100% 0px";

/**
 * A page image that failed is asked for again after this, twice as long each time it fails again,
 * up to the most: soon enough to come back with a busy server, rarely enough not to add to its load.
 */
export const IMAGE_RETRY_FIRST_MS = 2000;
export const IMAGE_RETRY_MOST_MS = 60_000;

/** A space the font has no width for is counted at this share of the size, as TeX sets it. */
export const MISSING_SPACE_EM = 0.25;
