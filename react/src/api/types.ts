import type { components } from "./schema";

type Schemas = components["schemas"];

// Not read: `expires_at`. Nothing shows the hour, and a document the server lost opens again by itself.
export type Document = Schemas["Document"];
export type DocumentNoticeInfo = Schemas["DocumentNoticeInfo"];
export type PageInfo = Schemas["PageInfo"];
export type SpanInfo = Schemas["SpanInfo"];
export type LineInfo = Schemas["LineInfo"];
export type Box = Schemas["Box"];
export type FontInfo = Schemas["FontInfo"];
export type Sentences = Schemas["Sentences"];
export type CountedSentence = Schemas["CountedSentence"];
export type FitRules = Schemas["FitRules"];
export type Replace = Schemas["Replace"];
export type Redact = Schemas["Redact"];
export type ReplaceRuns = Schemas["ReplaceRuns"];
export type RunText = Schemas["RunText"];
export type Insert = Schemas["Insert"];
export type DeletePage = Schemas["DeletePage"];
export type Edit = Replace | ReplaceRuns | Redact | Insert | DeletePage;
export type Region = Schemas["Region"];
export type RenderBody = Schemas["RenderBody"];
export type Render = Schemas["Render"];
export type ImageInfo = Schemas["ImageInfo"];
export type FitInfo = Schemas["FitInfo"];
export type RedactionInfo = Schemas["RedactionInfo"];
// No notice's `code`, `params` or `type` is read: each is shown as its `detail`, the server's sentence.
export type SpanNoticeInfo = Schemas["SpanNoticeInfo"];
export type FileNoticeInfo = Schemas["FileNoticeInfo"];
export type NoticeInfo = Render["notices"][number];
export type SkippedInfo = Schemas["SkippedInfo"];
export type ProblemInfo = Schemas["ProblemInfo"];
export type Problem = Omit<ProblemInfo, "type"> & { type: ProblemInfo["type"] | "unreachable" };
export type Strategy = Schemas["Strategy"];
export type FontList = Schemas["FontList"];
export type Terms = Schemas["Terms"];
export type TermsPiece = Schemas["TermsWords"] | Schemas["TermsEmail"];
export type SignedIn = Schemas["SignedIn"];
export type DocumentsLeft = Schemas["DocumentsLeft"];
export type AccountState = Schemas["AccountState"];
export type PaymentPage = Schemas["PaymentPage"];
export type LinkSent = Schemas["LinkSent"];
