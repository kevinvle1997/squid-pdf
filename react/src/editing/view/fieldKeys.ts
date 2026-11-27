// The keys a field typed into answers, a span's or a line's: the same in each, as one field.
import type { KeyboardEvent } from "react";
import type { Editor } from "../editor";
import type { EditableValue } from "../formattedValues";
import { enter, finish, stepWayOut } from "../typing";
import { openValueEditor } from "../valueEditor";

/** A key in the field: Alt+Down opens its value's editor, Up and Down choose how a long line fits, Enter keeps, Escape stops. */
export function fieldKeyDown(
  editor: Editor,
  event: KeyboardEvent,
  { value }: { value: EditableValue | undefined },
): void {
  // Alt+Down opens a picker, as it does in any field that has one.
  if (event.key === "ArrowDown" && event.altKey && openValueEditor(editor, value)) {
    event.preventDefault();
    return;
  }
  // Up and Down choose how a long line fits; while it fits, they're the field's own.
  const step = { ArrowUp: -1, ArrowDown: 1 } as const;
  if ((event.key === "ArrowUp" || event.key === "ArrowDown") && stepWayOut(editor, step[event.key])) {
    event.preventDefault();
    return;
  }
  if (event.key !== "Enter" && event.key !== "Escape") return;
  event.preventDefault();
  if (event.key === "Enter") enter(editor);
  else finish(editor, false, { returnFocus: true });
}
