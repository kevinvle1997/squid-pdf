import type { KeyboardEvent } from "react";
import { flushSync } from "react-dom";
import type { Editor } from "../editor";
import type { EditableValue } from "../formattedValues";
import { splitOrJoin } from "../splitting";
import { enter, finish, stepWayOut } from "../typing";
import { openValueEditor } from "../valueEditor";
import { selectIn, selectionIn } from "./lineSelection";

/** A key in the field: Alt+Down opens its value's editor, Up and Down choose how a long line fits, Shift+Enter splits, Enter keeps, Escape stops. */
export function fieldKeyDown(
  editor: Editor,
  event: KeyboardEvent,
  { value }: { value: EditableValue | undefined },
): void {
  if (event.key === "ArrowDown" && event.altKey && openValueEditor(editor, value)) {
    event.preventDefault();
    return;
  }
  const step = { ArrowUp: -1, ArrowDown: 1 } as const;
  if ((event.key === "ArrowUp" || event.key === "ArrowDown") && stepWayOut(editor, step[event.key])) {
    event.preventDefault();
    return;
  }
  if (event.key !== "Enter" && event.key !== "Escape") return;
  event.preventDefault();
  if (event.key === "Enter" && event.shiftKey && !(event.metaKey || event.ctrlKey || event.altKey)) {
    if (!event.nativeEvent.isComposing) splitAtCaret(editor, event.currentTarget);
    return;
  }
  if (event.key === "Enter") enter(editor);
  else finish(editor, false, { returnFocus: true });
}

/** Split or join at the field's caret, which then shows where it was, in this field or the one that takes its place. */
export function splitAtCaret(editor: Editor, field: EventTarget | null): void {
  if (!(field instanceof HTMLElement)) return;
  const input = field instanceof HTMLInputElement ? field : null;
  const at = input === null ? selectionIn(field)?.[0] : input.selectionStart;
  if (at == null) return;
  let caret: number | null = null;
  // Drawn before it returns, so a field that stays has its new words for the caret to go in.
  flushSync(() => {
    caret = splitOrJoin(editor, at);
  });
  const to = caret as number | null;
  // One put in its place sets its own caret as it takes focus.
  if (to === null || !field.isConnected) return;
  if (input === null) selectIn(field, to, to);
  else input.setSelectionRange(to, to);
}

/** What Cmd+Z, Cmd+Shift+Z and Ctrl+Y ask of a line field's typing, as an input's keys do: none for any other key. */
export function typingStepOf(event: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "shiftKey" | "altKey">) {
  if (!(event.metaKey || event.ctrlKey) || event.altKey) return null;
  const key = event.key.toLowerCase();
  if (key === "z") return event.shiftKey ? "redo" : "undo";
  return key === "y" && !event.shiftKey ? "redo" : null;
}
