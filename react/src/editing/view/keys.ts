// The keyboard as the page has it: what this platform calls the key a shortcut is held with, and
// where a key was pressed. Read off the DOM, which node hasn't, so tested in the browser.
const APPLE = /Mac|iPhone|iPad/.test(navigator.userAgent);
export const COMMAND = APPLE ? "⌘" : "Ctrl ";
export const ALT = APPLE ? "⌥" : "Alt ";

/** Whether a key was pressed in a text field, whose own keys come before a shortcut's. */
export function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
}

/** The page a key was pressed on, anything on it or its tick in the page list: each says which, as `data-page`. */
export function pageOn(target: EventTarget | null): number | null {
  const on = target instanceof HTMLElement ? target.closest<HTMLElement>("[data-page]") : null;
  return on?.dataset.page === undefined ? null : Number(on.dataset.page);
}

/** The span a key was pressed on: its mark says which, as `data-span`. */
export function spanOn(target: EventTarget | null): string | null {
  return target instanceof HTMLElement ? (target.dataset.span ?? null) : null;
}
