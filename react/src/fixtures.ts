// Made-up API replies for tests, whole and typed. A field the server adds breaks here, where
// `npm run types` brings it in, instead of hiding behind a cast in every test. Tests only.
import type {
  Document,
  DocumentNoticeInfo,
  FileNoticeInfo,
  FitInfo,
  FitRules,
  FontInfo,
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
  will_leave_out: "{letters} can't be drawn in any font we have, and will be left out",
  too_long: "about {percent}% too long for its place: shorten it, or it runs into what's beside it",
  substitute: "Edits here use {font}, which may be a different width from the original.",
  substitute_same_widths: "Edits here use {font}, whose letters are the same width as the original's.",
  drawn_in_your_copy: "Edits here use your copy of this font.",
  drawn_in_own_copy: "Edits here use the file's own copy of this font.",
  undo_redaction: "This text is redacted. Editing it undoes the redaction. Edit it anyway?",
  reopened: "The server no longer had this document, so it was opened again from this browser.",
  export_left_out: "Downloaded, but some changes were left out: they point at text that isn't in this document.",
  form_field_not_edited: "This text is a form field, which can't be edited yet, so an edit here is left out.",
  empty: "This text can't be left empty. Type what it should say, or leave the field to keep it as it was.",
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
    letter_spacing_em: 0,
    room_pt: 0,
    form_field: false,
    ...fields,
  };
}

/** A font the file's own copy of can draw with, unless `substitute` names what stands in. */
export function aFont(name: string, fields: Partial<FontInfo> = {}): FontInfo {
  return {
    object: null, // no object: its spans name it by its name alone, as aSpan's do
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
    fit: RULES,
    sentences: SENTENCES,
    notices: [],
    ...fields,
  };
}

/** The server's fit for a replacement that went in as typed. */
export function aFit(fields: Partial<FitInfo> = {}): FitInfo {
  return {
    delta_pt: 0,
    missing: [],
    left_out: [],
    options: [],
    strategy: "as-is",
    message: null,
    message_parts: [],
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

/** An edit that puts a new line on a page, set at its baseline. */
export function anInsert(fields: Partial<Insert> = {}): Insert {
  return { kind: "insert", page: 0, origin: [72, 400], text: "Signed", size: 10, ...fields };
}

/** The server's verdict on a redaction: its words gone, and nothing else to say. */
export function aRedaction(fields: Partial<RedactionInfo> = {}): RedactionInfo {
  return { verified: true, hidden_copies: [], message: null, ...fields };
}

/** What opening the file found, in the server's words. */
export function aDocumentNotice(detail: string, code = "notice"): DocumentNoticeInfo {
  return { code, params: {}, type: "t", detail };
}
