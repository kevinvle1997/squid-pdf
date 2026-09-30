// Made-up API replies for tests, whole and typed. A field the server adds breaks here, where
// `npm run types` brings it in, instead of hiding behind a cast in every test. Tests only.
import type {
  Copy,
  Document,
  FitInfo,
  FitRules,
  FontInfo,
  NoticeInfo,
  PageInfo,
  ProblemInfo,
  Render,
  SkippedInfo,
  SpanInfo,
} from "./api/types";

/** An A4 page, upright, in points. */
export const A4: PageInfo = { width: 595, height: 842, rotation: 0 };

/** The server's thresholds, as core/constants.py sets them. */
export const RULES: FitRules = { tolerance_pt: 4, condense_limit: 0.05, shrink_floor: 0.9 };

/** The server's sentences, as core/words/en.toml writes them. */
export const COPY: Copy = {
  missing: "no {chars} in this font, so the line is drawn in {font}",
  too_long: "{delta_pt} pt too long",
  stand_in: "Edits here use {font}, which may be a different width from the original.",
  stand_in_same_widths: "Edits here use {font}, whose letters are the same width as the original's.",
  undo_redaction: "This text is redacted. Editing it undoes the redaction. Edit it anyway?",
  reopened: "This document's hour ran out, so it was opened again from this browser.",
  export_left_out: "Downloaded, but some changes were left out: they point at text that isn't in this document.",
  options: {},
  approximate: {},
};

/** A line of ten-point Times near the top of the first page, reading "was <id>". */
export function aSpan(fields: Partial<SpanInfo> & Pick<SpanInfo, "id">): SpanInfo {
  return {
    page: 0,
    text: `was ${fields.id}`,
    font: "Times-Roman",
    size: 10,
    color: [0, 0, 0],
    bbox: { x0: 72, y0: 100, x1: 200, y1: 112 },
    origin: [72, 110],
    fidelity: "exact",
    why: null,
    ...fields,
  };
}

/** A font the file's own copy of can draw with, unless `substitute` names what stands in. */
export function aFont(name: string, fields: Partial<FontInfo> = {}): FontInfo {
  return {
    name,
    substitute: null,
    why: null,
    why_code: null,
    why_params: {},
    same_widths: true,
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
    copy: COPY,
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
    redactions: [],
    skipped: [],
    notices: [],
    build: "build",
    expires_at: "2026-01-01T00:00:00Z",
    ...fields,
  };
}

/** Something the server did other than asked, in its words. */
export function aNotice(detail: string, fields: Partial<NoticeInfo> = {}): NoticeInfo {
  return { code: "notice", params: {}, span_id: null, detail, edit: null, ...fields };
}

/** An edit the server left out, at `edit` in the list sent. */
export function aSkipped(edit: number, detail: string): SkippedInfo {
  return { code: "no_span", params: {}, edit, type: "bad-reference", detail };
}

/** A Problem the server answered with. */
export function aProblem(status: number, detail = "said"): ProblemInfo {
  return { type: "problem", status, detail, code: "problem", params: {} };
}
