// The keyboard as the page has it: what this platform calls the key a shortcut is held with, and
// where a key or a press landed. Read off the DOM, which node hasn't, so tested in the browser.
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

/** Whether a press on a page's layer landed on bare paper: the layer itself, or its image or a strip drawn on it. */
export function onBarePaper(target: EventTarget | null, layer: Element): boolean {
  return target === layer || (target instanceof HTMLImageElement && target.parentElement === layer);
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
