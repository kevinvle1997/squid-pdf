// The shapes the API sends and takes, named. All of them come from schema.d.ts,
// which `npm run types` writes from the server's OpenAPI.
import type { components } from "./schema";

type Schemas = components["schemas"];

export type Document = Schemas["Document"];
export type PageInfo = Schemas["PageInfo"];
export type SpanInfo = Schemas["SpanInfo"];
export type FontInfo = Schemas["FontInfo"];
export type Copy = Schemas["Copy"];
export type FitRules = Schemas["FitRules"];
export type Replace = Schemas["Replace"];
export type Redact = Schemas["Redact"];
export type Insert = Schemas["Insert"];
export type Edit = Replace | Redact | Insert;
export type Region = Schemas["Region"];
export type RenderBody = Schemas["RenderBody"];
export type Render = Schemas["Render"];
export type ImageInfo = Schemas["ImageInfo"];
export type FitInfo = Schemas["FitInfo"];
export type SpanNoticeInfo = Schemas["SpanNoticeInfo"];
export type InsertNoticeInfo = Schemas["InsertNoticeInfo"];
export type FileNoticeInfo = Schemas["FileNoticeInfo"];
export type NoticeInfo = SpanNoticeInfo | InsertNoticeInfo | FileNoticeInfo;
export type SkippedInfo = Schemas["SkippedInfo"];
export type ProblemInfo = Schemas["ProblemInfo"];
/** What went wrong: a Problem the server sent, or the browser's own when nothing answered. */
export type Problem = Omit<ProblemInfo, "type"> & { type: ProblemInfo["type"] | "unreachable" };
export type Strategy = Schemas["Strategy"];
