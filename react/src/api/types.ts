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
export type NoticeInfo = Schemas["NoticeInfo"];
export type ProblemInfo = Schemas["ProblemInfo"];
export type Strategy = Schemas["Strategy"];
