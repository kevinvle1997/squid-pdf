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

/** New text's field is never narrower than this, a share of its size: room for a couple of letters, so an empty one shows. */
export const NEW_TEXT_FIELD_MIN_EM = 2;

/** A new line has no box until it's drawn: an em above its baseline and this far below covers any face we ship. */
export const NEW_TEXT_ASCENT_EM = 1;
export const NEW_TEXT_DESCENT_EM = 0.3;

/** New text on a page with none to take its style from: a plain face we ship, at a usual size for a document's body, in black. */
export const NEW_TEXT_FACE = "Liberation Sans Regular";
export const NEW_TEXT_SIZE_PT = 11;

/**
 * New text in a handwriting face is drawn this much larger than the text it's styled from. Handwriting faces set
 * their lowercase small, so the step brings a signature up to the size of the text around it.
 */
export const HANDWRITING_STEP = 1.3;

/** New text on the line below a span starts this far under its baseline, in its size: the usual leading of set type. */
export const LINE_BELOW_EM = 1.2;

/** New text on a page with no text opens this far in from its top left: an inch, a page's usual margin. */
export const NEW_TEXT_MARGIN_PT = 72;

/** A second tap on the same span, or bare paper, within this is a double tap: it edits, or opens new text. A mouse click edits at once. */
export const DOUBLE_TAP_MS = 400;

/** A second tap on bare paper this near the first, in CSS pixels, is on the same spot: a finger's width. */
export const DOUBLE_TAP_REACH_PX = 24;

/** How long a pointer rests on a span before its note shows. Focus shows it at once. */
export const NOTE_DELAY_MS = 250;

/** How long a span's note stays once the pointer leaves its mark or the note: time to cross between them. React Aria's own `closeDelay`, so the margin's notes close alike. */
export const NOTE_CLOSE_DELAY_MS = 500;

/** Find runs, and "Change to" is counted, once typing stops this long: soon enough to feel live, late enough to skip each letter. */
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

/** How far below its button a list from the bar opens: --s3, more than a button centred in the bar has below it, so it opens under the bar. */
export const BELOW_BAR_PX = 12;

/** The shares of its printed size a page can be zoomed to: as far out as a page and a half side by side, as far in as a line's words fill a phone. */
export const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3] as const;

/** A step this close to the share shown, as a share of it, is skipped: zooming in from 45% goes to 75%, not 50%. */
export const ZOOM_SKIP = 1.15;

/** Languages for dates are learnt in slices this long, between other work: well inside a frame, so nothing waits on them. */
export const LEARN_SLICE_MS = 8;

/** How many language codes are asked of Intl at once while learning: few enough that the ask stays inside a slice. */
export const LEARN_BATCH = 10;
