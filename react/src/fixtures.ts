// Made-up API replies for tests, whole and typed. A field the server adds breaks here, where
// `npm run types` brings it in, instead of hiding behind a cast in every test. Tests only.
import type {
  CountedSentence,
  DeletePage,
  Document,
  DocumentNoticeInfo,
  FileNoticeInfo,
  FitInfo,
  FitRules,
  FontInfo,
  FontList,
  Insert,
  PageInfo,
  ProblemInfo,
  Redact,
  RedactionInfo,
  Render,
  Replace,
  Sentences,
  SkippedInfo,
  SpanInfo,
  SpanNoticeInfo,
} from "./api/types";

/** An A4 page, upright, in points. */
export const A4: PageInfo = { width: 595, height: 842, turn_cw: 0 };

/** The server's thresholds, as core/constants.py sets them. */
export const RULES: FitRules = { tolerance_pt: 4, room_slack_pt: 0.1, condense_limit: 0.05, shrink_floor: 0.9 };

/** The server's sentences, as core/app/words/en.toml writes them. */
export const SENTENCES: Sentences = {
  missing: "no {chars} in this font, so the line is drawn in our {font}",
  not_in_font: "no {chars} in this font",
  will_leave_out: "{letters} can't be drawn in any font we have, and will be left out",
  too_long: "about {percent}% too long for its place: shorten it, or it runs into what's beside it",
  substitute: "Edits here use {font}, which may be a different width from the original.",
  substitute_same_widths: "Edits here use {font}, whose letters are the same width as the original's.",
  drawn_in_your_copy: "Edits here use your copy of this font.",
  drawn_in_own_copy: "Edits here use the file's own copy of this font.",
  undo_redaction: "This text is redacted. Editing it undoes the redaction. Edit it anyway?",
  reopened: "The server no longer had this document, so it was opened again from this browser.",
  export_left_out: "Downloaded, but some changes were left out: they point at text that isn't in this document.",
  page_left_out: "This page is left out of the download",
  page_put_back: "Page {page} is back in the download",
  how_to_fix: "Click any word to fix it.",
  how_to_fix_touch: "Tap any word to fix it.",
  how_to_add: "Double-click any blank spot to add text.",
  how_to_add_touch: "Double-tap any blank spot to add text.",
  new_text: "New text",
  new_text_moves:
    "Drag it to move it, or nudge it with the arrow keys, Shift for a bigger step. Enter types in it again.",
  new_text_moves_touch: "Drag it to move it. Double-tap to type in it again.",
  form_field_not_edited: "This text is a form field, which can't be edited yet, so an edit here is left out.",
  email_broken: "the email address here no longer reads as one",
  empty: "This text can't be left empty. Type what it should say, or leave the field to keep it as it was.",
  edit_breaks_signature: "This PDF is signed, so this change breaks its signature.",
  back_to: "Back to \u201c{text}\u201d.",
  changed_to: "Changed to \u201c{text}\u201d.",
  put_back: "Put back \u201c{text}\u201d.",
  redacted: "Redacted \u201c{text}\u201d.",
  new_text_added: "Added \u201c{text}\u201d.",
  new_text_moved: "Moved \u201c{text}\u201d.",
  new_text_taken_out: "Took out \u201c{text}\u201d.",
  new_text_set_in: "Set in {font}.",
  your_copy_in_use: "Your copy of {font} is in use.",
  your_copy_removed: "Your copy of {font} is removed.",
  warning_muted: "Edits in {font} won't warn again.",
  warning_unmuted: "Edits in {font} warn again.",
  downloaded: "Downloaded {name}.",
  find_nothing: "Nothing matches",
  find_identical: { one: "{count} place will look identical", other: "{count} places will look identical" },
  find_different: { one: "{count} place will look different", other: "{count} places will look different" },
  find_places: { one: "{count} place, ", other: "{count} places, " },
  find_some_different: { one: "{count} of them will look different", other: "{count} of them will look different" },
  find_empty: { one: "{count} place can't be left empty", other: "{count} places can't be left empty" },
  find_some_empty: { one: "{count} of them can't be left empty", other: "{count} of them can't be left empty" },
  changed_places: { one: "Changed {count} place.", other: "Changed {count} places." },
  changes: { one: "{count} change", other: "{count} changes" },
  changes_different: { one: "{count} change will look different", other: "{count} changes will look different" },
  changes_similar: { one: "{count} change in a similar font", other: "{count} changes in a similar font" },
  changes_some: { one: "{count} change, ", other: "{count} changes, " },
  changes_some_different: {
    one: "{count} of them will look different",
    other: "{count} of them will look different",
  },
  changes_some_similar: { one: "{count} of them in a similar font", other: "{count} of them in a similar font" },
  at_rest_different: {
    one: "Edits in {count} place will look different",
    other: "Edits in {count} places will look different",
  },
  at_rest_similar: {
    one: "Edits in {count} place will use a similar font",
    other: "Edits in {count} places will use a similar font",
  },
  changes_too_long: { one: "{count} too long for its place", other: "{count} too long for their places" },
  typed_identical: "This edit will look identical.",
  typed_different: "This edit will look different.",
  typed_similar: "This edit will use a similar font.",
  join_parts: "; ",
  options: {
    shrink: { label: "Make it slightly smaller", detail: "Reduces the type size just enough to fit." },
    condense: {
      label: "Tighten the letters",
      detail: "Squeezes the spacing by a few percent, which isn't noticeable at this size.",
    },
    "as-is": { label: "Leave it long", detail: "Runs {delta_pt} pt past where the original ended." },
  },
  approximate: {
    turned_text: "This text is turned on the page, and edits are drawn level, so an edit here will look different.",
    undrawable_letters: "No font we have can draw {letters} here, so an edit here leaves them out.",
  },
  characters: {
    "\u00ad": "optional hyphen",
    "\u202f": "narrow no-break space",
    "\u0301": "acute accent",
    "0": "zero",
    I: "capital I",
    O: "capital O",
    l: "lowercase l",
  },
};

// A placeholder, which a sentence shouted keeps as it is.
const PLACEHOLDER = /(\{\w+\})/;

/** `english` shouted, its placeholders as they are. */
function shouted(english: string): string {
  const parts = english.split(PLACEHOLDER);
  return parts.map((part) => (PLACEHOLDER.test(part) ? part : part.toUpperCase())).join("");
}

/** Each form of a sentence with a count, shouted. */
function shoutedForms({ one, other }: CountedSentence): CountedSentence {
  return { one: shouted(one), other: shouted(other) };
}

/**
 * The server's sentences in a language made up for tests, as tests/conftest.py's `pseudo` makes one: those the
 * browser fills and says of an edit, a font, a download, Find or the bar's count, shouted. A sentence the browser
 * wrote itself reads unlike them.
 */
export const PSEUDO_SENTENCES: Sentences = {
  ...SENTENCES,
  back_to: shouted(SENTENCES.back_to),
  changed_to: shouted(SENTENCES.changed_to),
  put_back: shouted(SENTENCES.put_back),
  redacted: shouted(SENTENCES.redacted),
  new_text_added: shouted(SENTENCES.new_text_added),
  new_text_moved: shouted(SENTENCES.new_text_moved),
  new_text_taken_out: shouted(SENTENCES.new_text_taken_out),
  new_text_set_in: shouted(SENTENCES.new_text_set_in),
  your_copy_in_use: shouted(SENTENCES.your_copy_in_use),
  your_copy_removed: shouted(SENTENCES.your_copy_removed),
  warning_muted: shouted(SENTENCES.warning_muted),
  warning_unmuted: shouted(SENTENCES.warning_unmuted),
  downloaded: shouted(SENTENCES.downloaded),
  find_nothing: shouted(SENTENCES.find_nothing),
  find_identical: shoutedForms(SENTENCES.find_identical),
  find_different: shoutedForms(SENTENCES.find_different),
  find_places: shoutedForms(SENTENCES.find_places),
  find_some_different: shoutedForms(SENTENCES.find_some_different),
  find_empty: shoutedForms(SENTENCES.find_empty),
  find_some_empty: shoutedForms(SENTENCES.find_some_empty),
  changed_places: shoutedForms(SENTENCES.changed_places),
  changes: shoutedForms(SENTENCES.changes),
  changes_different: shoutedForms(SENTENCES.changes_different),
  changes_similar: shoutedForms(SENTENCES.changes_similar),
  changes_some: shoutedForms(SENTENCES.changes_some),
  changes_some_different: shoutedForms(SENTENCES.changes_some_different),
  changes_some_similar: shoutedForms(SENTENCES.changes_some_similar),
  at_rest_different: shoutedForms(SENTENCES.at_rest_different),
  at_rest_similar: shoutedForms(SENTENCES.at_rest_similar),
  changes_too_long: shoutedForms(SENTENCES.changes_too_long),
  typed_identical: shouted(SENTENCES.typed_identical),
  typed_different: shouted(SENTENCES.typed_different),
  typed_similar: shouted(SENTENCES.typed_similar),
  join_parts: " / ",
};

/** A line of ten-point Times near the top of the first page, reading "was <id>". */
export function aSpan(fields: Partial<SpanInfo> & Pick<SpanInfo, "id">): SpanInfo {
  return {
    page: 0,
    text: `was ${fields.id}`,
    font: "Times-Roman",
    font_object: null, // no object: its font goes by its name alone, as aFont's does
    size: 10,
    color: [0, 0, 0],
    bbox: { x0: 72, y0: 100, x1: 200, y1: 112 },
    origin: [72, 110],
    fidelity: "exact",
    why: null,
    substitute: null,
    same_widths: false,
    in_file: true,
    letter_spacing_em: 0,
    room_pt: 0,
    form_field: false,
    ...fields,
  };
}

/** A font the file's own copy of can draw with, unless `substitute` names what stands in. */
export function aFont(name: string, fields: Partial<FontInfo> = {}): FontInfo {
  return {
    font_object: null, // no object: its spans name it by its name alone, as aSpan's do
    name,
    shown_name: name.replace(/^[A-Z]{6}\+/, ""), // as the server shows it: a trimmed copy's prefix aside
    substitute: null,
    why: null,
    why_code: null,
    why_params: {},
    same_widths: false, // as the server sends it when the font's own copy draws: nothing stands in to match
    look_alike: "Liberation Serif Regular", // Times's, as the server matches it
    attached: false,
    glyphs: {},
    ...fields,
  };
}

/** A document of A4 pages, as many as its spans are on, unless `pages` says. */
export function aDoc(fields: Partial<Document> = {}): Document {
  const spans = fields.spans ?? [];
  const pageCount = Math.max(1, ...spans.map((span) => span.page + 1));
  return {
    id: "doc",
    build: "build",
    expires_at: "2026-01-01T00:00:00Z",
    pages: Array.from({ length: pageCount }, () => A4),
    spans,
    fonts: [],
    signed: false,
    fit: RULES,
    sentences: SENTENCES,
    notices: [],
    ...fields,
  };
}

/** The server's fit for a replacement that went in as typed, in the file's own font. */
export function aFit(fields: Partial<FitInfo> = {}): FitInfo {
  return {
    delta_pt: 0,
    missing: [],
    left_out: [],
    options: [],
    strategy: "as-is",
    message: null,
    message_parts: [],
    fidelity: "exact",
    why: null,
    substitute: null,
    same_widths: false,
    ...fields,
  };
}

/** A render reply that drew nothing and had nothing to say. */
export function aReply(fields: Partial<Render> = {}): Render {
  return {
    images: [],
    fits: {},
    insert_fits: [],
    redactions: {},
    skipped: [],
    notices: [],
    build: "build",
    expires_at: "2026-01-01T00:00:00Z",
    ...fields,
  };
}

/** A span's edit the server drew other than asked, in its words. */
export function aSpanNotice(detail: string, spanId = "span"): SpanNoticeInfo {
  return { kind: "span", span_id: spanId, code: "notice", params: {}, detail };
}

/** What saving did to the whole file other than asked, in the server's words. */
export function aFileNotice(detail: string): FileNoticeInfo {
  return { kind: "file", code: "notice", params: {}, detail };
}

/** An edit the server left out, at `edit` in the list sent. */
export function aSkipped(edit: number, detail: string): SkippedInfo {
  return { code: "no_span", params: {}, edit, type: "bad_reference", detail };
}

/** A Problem the server answered with. */
export function aProblem(status: number, detail = "said"): ProblemInfo {
  return { type: "server_error", status, detail, code: "server_error", params: {} };
}

/** Widths for `letters`, each half the size wide, so a width is easy to work out: 10 letters at 10 pt is 50 pt. */
export function halfWideGlyphs(letters: string): Record<string, number> {
  return Object.fromEntries([...letters].map((letter) => [letter, 500]));
}

/** An edit that replaces a span's words. */
export function aReplace(spanId: string, text = "now", fields: Partial<Replace> = {}): Replace {
  return { kind: "replace", span_id: spanId, text, ...fields };
}

/** An edit that takes a span's words out of the file. */
export function aRedact(spanId: string): Redact {
  return { kind: "redact", span_id: spanId };
}

/** An edit that leaves page `page` out of the file, by its number in the original. */
export function aDeletePage(page: number): DeletePage {
  return { kind: "delete_page", page };
}

/** An edit that puts a new line on a page, set at its baseline. */
export function anInsert(fields: Partial<Insert> = {}): Insert {
  return { kind: "insert", page: 0, origin: [72, 400], text: "Signed", size: 10, ...fields };
}

/** The server's verdict on a redaction: its words gone, and nothing else to say. */
export function aRedaction(fields: Partial<RedactionInfo> = {}): RedactionInfo {
  return { verified: true, hidden_copies: [], message: null, ...fields };
}

/** What opening the file found, in the server's words, its type its sentence's key as the server sends it. */
export function aDocumentNotice(detail: string, type = "notice"): DocumentNoticeInfo {
  return { code: type, params: {}, type, detail };
}

/** The server's font list, cut to a face of each kind: the handwriting faces we ship, and a plain one. */
export function aFontList(): FontList {
  const face = (name: string, style: "regular" | "bold" = "regular") => ({ name, style, glyphs: {} });
  const family = (name: string, category: "sans" | "handwriting", faces: FontList["families"][number]["faces"]) => ({
    family: name,
    category,
    license: "OFL-1.1",
    same_widths_as: [],
    faces,
  });
  return {
    build: "build",
    families: [
      family("Liberation Sans", "sans", [face("Liberation Sans Regular")]),
      family("Caveat", "handwriting", [face("Caveat Regular"), face("Caveat Bold", "bold")]),
      family("Great Vibes", "handwriting", [face("Great Vibes Regular")]),
    ],
  };
}
