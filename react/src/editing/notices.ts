// The lines under the bar, by where each came from, since each clears on its own terms. The render's
// live with what it drew (`Drawn`), so a page's notices go when its strips do.
import type { Drawn } from "./render";

/** A line under the bar about what just happened. */
export interface Notice {
  readonly tone: "plain" | "warn";
  readonly text: string;
}

/** Why the user's copy of a font was refused, said beside that font too. */
export interface FontNotice extends Notice {
  readonly font: string; // the document's font, by its listed name
}

export interface Notices {
  readonly document: readonly Notice[]; // what opening the file found: for as long as it's open
  readonly export: readonly Notice[]; // what the last export said: until the next change
  readonly reopen: Notice | null; // the document opened again, or couldn't: in the bar, until the next change
  readonly font: FontNotice | null; // the user's copy of a font refused: until the next change
  readonly drop: Notice | null; // a file dropped on the editor that didn't open: until the next change
}

export const NO_NOTICES: Notices = { document: [], export: [], reopen: null, font: null, drop: null };

/** The lines once the user changes the history: only what opening found outlasts it, the rest is stale. */
export function noticesAfterChange(notices: Notices): Notices {
  return { ...NO_NOTICES, document: notices.document };
}

export const warn = (text: string): Notice => ({ tone: "warn", text });
export const plain = (text: string): Notice => ({ tone: "plain", text });

/** Everything to say under the bar now, warnings first, each sentence once; reopening is the bar's own. */
export function noticeLines(notices: Notices, drawn: Drawn): Notice[] {
  const fromRender = [
    ...(drawn.failed === null ? [] : [drawn.failed]),
    ...[...drawn.notices.values()].flat().map((notice) => notice.detail),
    ...drawn.skipped.map((skipped) => skipped.detail),
  ].map(warn);
  const font = notices.font === null ? null : warn(notices.font.text);
  const all = [...notices.document, ...fromRender, ...notices.export, font, notices.drop].filter(
    (notice): notice is Notice => notice !== null,
  );
  const once = [...new Map(all.map((notice) => [notice.text, notice])).values()];
  return [...once.filter((notice) => notice.tone === "warn"), ...once.filter((notice) => notice.tone === "plain")];
}

/** Lines as one thing to hear, each in turn. */
export function heardIn(lines: readonly Notice[]): string {
  return lines.map((notice) => notice.text).join(" ");
}

/** The sentences `after` has that `before` didn't, as one thing to hear: empty when none is new. */
export function newlyHeard(before: readonly Notice[], after: readonly Notice[]): string {
  const known = new Set(before.map((notice) => notice.text));
  return heardIn(after.filter((notice) => !known.has(notice.text)));
}
