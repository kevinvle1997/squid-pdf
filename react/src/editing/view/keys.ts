import type { Selected } from "../redaction";
import { selectionIn } from "./lineSelection";

const APPLE = /Mac|iPhone|iPad/.test(navigator.userAgent);
export const COMMAND = APPLE ? "⌘" : "Ctrl ";
export const ALT = APPLE ? "⌥" : "Alt ";

/** A slip's own keys come before a shortcut's: undo there would undo another edit with this one still open. */
export function isTyping(target: EventTarget | null): boolean {
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return true;
  // A line's field, its runs each in their own font.
  if (target instanceof HTMLElement && target.isContentEditable) return true;
  return target instanceof Element && target.closest("[data-slip]") !== null;
}

export function selectedIn(target: EventTarget | null): Selected | null {
  const lineField = target instanceof HTMLElement && target.isContentEditable && target.dataset.spanField !== undefined;
  if (lineField) {
    const selected = selectionIn(target);
    return selected === null || selected[0] === selected[1] ? null : selected;
  }
  const spanField = target instanceof HTMLInputElement && target.dataset.spanField !== undefined;
  if (!spanField) return null;
  const { selectionStart, selectionEnd } = target;
  if (selectionStart === null || selectionEnd === null || selectionStart === selectionEnd) return null;
  return [selectionStart, selectionEnd];
}

export function onBarePaper(target: EventTarget | null, layer: Element): boolean {
  return target === layer || (target instanceof HTMLImageElement && target.parentElement === layer);
}

/** Not React Aria's `preventFocusOnPress`, which would keep the slip's own fields from taking focus too. */
export function keepFocusOnPress(event: Pick<MouseEvent, "target" | "preventDefault">): void {
  if (!(event.target instanceof HTMLInputElement)) event.preventDefault();
}

export function fellToNothing({ target, relatedTarget }: Pick<FocusEvent, "target" | "relatedTarget">): boolean {
  const holdsIt = relatedTarget instanceof Node && target instanceof Node && relatedTarget.contains(target);
  return relatedTarget === null || holdsIt;
}

export function pageOn(target: EventTarget | null): number | null {
  const on = target instanceof HTMLElement ? target.closest<HTMLElement>("[data-page]") : null;
  return on?.dataset.page === undefined ? null : Number(on.dataset.page);
}

export function newTextOn(target: EventTarget | null): number | null {
  const id = target instanceof HTMLElement ? target.dataset.newText : undefined;
  return id === undefined ? null : Number(id);
}

export function spanOn(target: EventTarget | null): string | null {
  return target instanceof HTMLElement ? (target.dataset.span ?? null) : null;
}

export function isOnMarginNote(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && target.dataset.marginNote !== undefined;
}
