// Thresholds the server owns come in Document.fit, never from here.

/** A page shows at its printed size on a 96 dpi screen. */
export const PX_PER_PT = 4 / 3;

export const MIN_SCALE = 1;
export const MAX_SCALE = 4;

/** So accents and underlines come with the strip. */
export const STRIP_PAD_PT = 2;

/** The most the server draws at once (`MAX_RENDER_PAGES`). */
export const RENDER_PAGES_AT_ONCE = 8;

/** Clear of the mark under the span. */
export const SLIP_GAP_PT = 4;

/** So the caret has room after the last letter. */
export const FIELD_SPARE_EM = 0.5;

/** Room for a couple of letters, so an empty field shows. */
export const NEW_TEXT_FIELD_MIN_EM = 2;

/** An em above the baseline and this far below covers any face we ship. */
export const NEW_TEXT_ASCENT_EM = 1;
export const NEW_TEXT_DESCENT_EM = 0.3;

export const NEW_TEXT_FACE = "Liberation Sans Regular";
export const NEW_TEXT_SIZE_PT = 11;

/** Handwriting faces set their lowercase small: this brings a signature up to the size of the text around it. */
export const HANDWRITING_STEP = 1.3;

/** The usual leading of set type. */
export const LINE_BELOW_EM = 1.2;

/** An inch, a page's usual margin. */
export const NEW_TEXT_MARGIN_PT = 72;

export const DOUBLE_TAP_MS = 400;

/** A finger's width. */
export const DOUBLE_TAP_REACH_PX = 24;

/** Enough to find the change in its line. */
export const NOTE_WORDS_AROUND = 1;

export const NOTE_DELAY_MS = 250;

/** Time to cross between mark and note; React Aria's own `closeDelay`, so the margin's notes close alike. */
export const NOTE_CLOSE_DELAY_MS = 500;

/** Soon enough to feel live, late enough to skip each letter. */
export const FIND_DELAY_MS = 200;

/** Longer than passing over Export on the way elsewhere, shorter than the reach to press it. */
export const EXPORT_INTENT_MS = 150;

export const LAZY_MARGIN = "100% 0px";

/** Doubled on each failure: soon enough for a busy server, rarely enough not to add to its load. */
export const IMAGE_RETRY_FIRST_MS = 2000;
export const IMAGE_RETRY_MOST_MS = 60_000;

/** As TeX sets it. */
export const MISSING_SPACE_EM = 0.25;

/** More than a button centred in the bar has below it, so the list opens under the bar. */
export const BELOW_BAR_PX = 12;

/** As far out as a page and a half side by side, as far in as a line's words fill a phone. */
export const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3] as const;

export const ZOOM_SKIP = 1.15;

/** Well inside a frame, so nothing waits on the learning. */
export const LEARN_SLICE_MS = 8;

/** Few enough that the ask stays inside a slice. */
export const LEARN_BATCH = 10;
