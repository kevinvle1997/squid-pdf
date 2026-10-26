// The shapes the API sends and takes, named, from schema.d.ts, which `npm run types` writes from the
// server's OpenAPI. A field the browser doesn't read says why beside its shape's name.
import type { components } from "./schema";

type Schemas = components["schemas"];

// Not read: `expires_at`. Nothing shows the hour, and a document the server lost opens again by itself.
export type Document = Schemas["Document"];
// Not read: `code`, `params` and `type`. Each is shown as its `detail`, the server's sentence.
export type DocumentNoticeInfo = Schemas["DocumentNoticeInfo"];
export type PageInfo = Schemas["PageInfo"];
// Not read: `origin`, since the preview sits in the span's box only until the strip, drawn at `origin`, replaces it;
// `fidelity`, since `substitute` is set exactly when it's "substitute", and `why` when it's "approximate".
export type SpanInfo = Schemas["SpanInfo"];
// Not read: `why_code` and `why_params`. The note says `why`, the server's sentence, as it is.
export type FontInfo = Schemas["FontInfo"];
export type Sentences = Schemas["Sentences"];
export type FitRules = Schemas["FitRules"];
export type Replace = Schemas["Replace"];
export type Redact = Schemas["Redact"];
export type Insert = Schemas["Insert"];
export type Edit = Replace | Redact | Insert;
export type Region = Schemas["Region"];
export type RenderBody = Schemas["RenderBody"];
export type Render = Schemas["Render"];
export type ImageInfo = Schemas["ImageInfo"];
// Not read: `options`, since the field offers the ways out from its own check, on the server's rules, as it's typed.
// `message_parts` is read only for its codes, to count the lines left long; the note says `message` whole.
export type FitInfo = Schemas["FitInfo"];
// Not read: `hidden_copies`. The note says `message`, which names each place already.
export type RedactionInfo = Schemas["RedactionInfo"];
// Not read, on any notice or skip: `code`, `params` and `type`. Each is shown as its `detail`, the server's sentence.
export type SpanNoticeInfo = Schemas["SpanNoticeInfo"];
export type FileNoticeInfo = Schemas["FileNoticeInfo"];
export type NoticeInfo = Render["notices"][number];
export type SkippedInfo = Schemas["SkippedInfo"];
// Not read: `type`, `code`, `params` and `debug`. A Problem is shown as its `detail`; `status` says one is gone.
export type ProblemInfo = Schemas["ProblemInfo"];
/** What went wrong: a Problem the server sent, or the browser's own when nothing answered. */
export type Problem = Omit<ProblemInfo, "type"> & { type: ProblemInfo["type"] | "unreachable" };
export type Strategy = Schemas["Strategy"];
