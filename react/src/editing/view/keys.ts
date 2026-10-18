// The keyboard as the page has it: what this platform calls the key a shortcut is held with, and
// where a key was pressed. Read off the DOM, which node hasn't, so tested in the browser.
const APPLE = /Mac|iPhone|iPad/.test(navigator.userAgent);
export const COMMAND = APPLE ? "⌘" : "Ctrl ";

/** Whether a key was pressed in a text field, whose own keys come before a shortcut's. */
export function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
}

/** The span a key was pressed on: its mark says which, as `data-span`. */
export function spanOn(target: EventTarget | null): string | null {
  return target instanceof HTMLElement ? (target.dataset.span ?? null) : null;
}
