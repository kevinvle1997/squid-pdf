// The keyboard as the page has it: what this platform calls the key a shortcut is held with, and
// where a key, a press or focus landed. Read off the DOM, which node hasn't, so tested in the browser.
import type { Selected } from "../redaction";

const APPLE = /Mac|iPhone|iPad/.test(navigator.userAgent);
export const COMMAND = APPLE ? "⌘" : "Ctrl ";
export const ALT = APPLE ? "⌥" : "Alt ";

/**
 * Whether a key was pressed in a text field, or in the slip of what's being typed (its calendar, new text's fonts),
 * whose own keys come before a shortcut's: undo there would undo another edit with this one still open.
 */
export function isTyping(target: EventTarget | null): boolean {
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return true;
  return target instanceof Element && target.closest("[data-slip]") !== null;
}

/** The words selected in a span's field a key was pressed in, start and end in its units: none outside one, or at a caret. */
export function selectedIn(target: EventTarget | null): Selected | null {
  const spanField = target instanceof HTMLInputElement && target.dataset.spanField !== undefined;
  if (!spanField) return null;
  const { selectionStart, selectionEnd } = target;
  if (selectionStart === null || selectionEnd === null || selectionStart === selectionEnd) return null;
  return [selectionStart, selectionEnd];
}

/** Whether a press on a page's layer landed on bare paper: the layer itself, or its image or a strip drawn on it. */
export function onBarePaper(target: EventTarget | null, layer: Element): boolean {
  return target === layer || (target instanceof HTMLImageElement && target.parentElement === layer);
}

/**
 * A press on a slip leaves focus where it is, in the field typed into or on the redaction prompt's button, unless it's
 * into a field of the slip's own (the amount's). Not React Aria's `preventFocusOnPress`, which would keep that field
 * from taking focus too.
 */
export function keepFocusOnPress(event: Pick<MouseEvent, "target" | "preventDefault">): void {
  if (!(event.target instanceof HTMLInputElement)) event.preventDefault();
}

/**
 * Whether focus leaving `target` fell to nothing: to the document, or to a box that holds it, such as the pages', as Tab
 * past all there is or a press on bare paper leaves it.
 */
export function fellToNothing({ target, relatedTarget }: Pick<FocusEvent, "target" | "relatedTarget">): boolean {
  const holdsIt = relatedTarget instanceof Node && target instanceof Node && relatedTarget.contains(target);
  return relatedTarget === null || holdsIt;
}

/** The page a key was pressed on, anything on it or its tick in the page list: each says which, as `data-page`. */
export function pageOn(target: EventTarget | null): number | null {
  const on = target instanceof HTMLElement ? target.closest<HTMLElement>("[data-page]") : null;
  return on?.dataset.page === undefined ? null : Number(on.dataset.page);
}

/** The new text a key was pressed on: its mark says which, as `data-new-text`. */
export function newTextOn(target: EventTarget | null): number | null {
  const id = target instanceof HTMLElement ? target.dataset.newText : undefined;
  return id === undefined ? null : Number(id);
}

/** The span a key was pressed on: its mark says which, as `data-span`. */
export function spanOn(target: EventTarget | null): string | null {
  return target instanceof HTMLElement ? (target.dataset.span ?? null) : null;
}

/** Whether a key was pressed on a margin note, beside the page or under it: its button has `data-margin-note`. */
export function isOnMarginNote(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && target.dataset.marginNote !== undefined;
}
