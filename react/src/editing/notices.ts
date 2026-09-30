// The lines under the bar, kept by where each came from, since each clears on its own terms.
// What the render said lives with what it drew (`Drawn`), so a page's notices go when its
// strips do; the rest are here.
import type { Drawn } from "./render";

/** A line under the bar about what just happened. */
export interface Notice {
  readonly tone: "plain" | "warn";
  readonly text: string;
}

export interface Notices {
  readonly document: readonly Notice[]; // what opening the file found: for as long as it's open
  readonly export: Notice | null; // the last export: until the next change
  readonly reopen: Notice | null; // the document opened again, or couldn't: until the next change
}

export const NO_NOTICES: Notices = { document: [], export: null, reopen: null };

export const warn = (text: string): Notice => ({ tone: "warn", text });
export const plain = (text: string): Notice => ({ tone: "plain", text });

/** Everything to say now, warnings first, each sentence once. */
export function noticeLines(notices: Notices, drawn: Drawn): Notice[] {
  const fromRender = [
    ...(drawn.failed === null ? [] : [drawn.failed]),
    ...[...drawn.notices.values()].flat().map((notice) => notice.detail),
    ...drawn.skipped.map((skipped) => skipped.detail),
  ].map(warn);
  const all = [...notices.document, ...fromRender, notices.export, notices.reopen].filter(
    (notice): notice is Notice => notice !== null,
  );
  const once = [...new Map(all.map((notice) => [notice.text, notice])).values()];
  return [...once.filter((notice) => notice.tone === "warn"), ...once.filter((notice) => notice.tone === "plain")];
}
