// By where each came from, since each clears on its own terms.
import type { Document } from "../api/types";
import type { Reading } from "./project";
import type { Drawn } from "./render";

export interface Notice {
  readonly tone: "plain" | "warn";
  readonly text: string;
}

export interface OpeningNotice extends Notice {
  readonly type: string;
}

export interface FontNotice extends Notice {
  readonly font: string;
}

export interface Notices {
  readonly document: readonly OpeningNotice[];
  readonly export: readonly Notice[];
  readonly reopen: Notice | null;
  readonly font: FontNotice | null;
  readonly drop: Notice | null;
}

export const NO_NOTICES: Notices = { document: [], export: [], reopen: null, font: null, drop: null };

export function noticesAfterChange(notices: Notices, { stale }: { stale: boolean }): Notices {
  return { ...NO_NOTICES, document: notices.document, reopen: stale ? notices.reopen : null };
}

export const warn = (text: string): Notice => ({ tone: "warn", text });
export const plain = (text: string): Notice => ({ tone: "plain", text });

export function openingNoticesOf(doc: Pick<Document, "notices">): OpeningNotice[] {
  return doc.notices.map((notice) => ({ ...warn(notice.detail), type: notice.type }));
}

export function openingLines(notices: Notices, { edits }: Pick<Reading, "edits">): readonly OpeningNotice[] {
  if (!edits.some((edit) => edit.kind === "redact")) return notices.document;
  return notices.document.filter((notice) => notice.type !== "signed_file");
}

export function noticeLines(notices: Notices, drawn: Drawn, reading: Pick<Reading, "deleted" | "edits">): Notice[] {
  const { deleted } = reading;
  const kept = [...drawn.notices].filter(([page]) => !deleted.has(page));
  const fromRender = [
    ...(drawn.failed === null ? [] : [drawn.failed]),
    ...kept.flatMap(([, onPage]) => onPage).map((notice) => notice.detail),
    ...drawn.skipped.map((skipped) => skipped.detail),
  ].map(warn);
  const font = notices.font === null ? null : warn(notices.font.text);
  const all = [...openingLines(notices, reading), ...fromRender, ...notices.export, font, notices.drop].filter(
    (notice): notice is Notice => notice !== null,
  );
  const once = [...new Map(all.map((notice) => [notice.text, notice])).values()];
  return [...once.filter((notice) => notice.tone === "warn"), ...once.filter((notice) => notice.tone === "plain")];
}

export function heardIn(lines: readonly Notice[]): string {
  return lines.map((notice) => notice.text).join(" ");
}

export function newlyHeard(before: readonly Notice[], after: readonly Notice[]): string {
  const known = new Set(before.map((notice) => notice.text));
  return heardIn(after.filter((notice) => !known.has(notice.text)));
}
