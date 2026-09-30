// Keyboard shortcuts, wherever focus is: one table, read by one listener in EditorShell, so a
// new binding is a row. Cmd+F, Cmd+Delete and the arrows join it with find and redaction.
import { change, type Editor } from "./editor";
import { exportNow } from "./export";

export interface Command {
  readonly keys: string; // as "mod+shift+z"; mod is Cmd or Ctrl, either on any platform
  readonly whileTyping: boolean; // in a text field too, rather than the field's own
  readonly run: (editor: Editor) => void;
}

const redo = (editor: Editor) => change(editor, { kind: "redo" });

export const COMMANDS: readonly Command[] = [
  // Export takes the words being typed along.
  { keys: "mod+s", whileTyping: true, run: (editor) => void exportNow(editor) },
  // In a text field, undo and redo are the field's own.
  { keys: "mod+z", whileTyping: false, run: (editor) => change(editor, { kind: "undo" }) },
  { keys: "mod+shift+z", whileTyping: false, run: redo },
  { keys: "mod+y", whileTyping: false, run: redo },
];

type KeyPress = Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "shiftKey" | "altKey">;

/** A key press as the table writes one: "mod+shift+z". */
export function keysOf(press: KeyPress): string {
  const held = [(press.metaKey || press.ctrlKey) && "mod", press.altKey && "alt", press.shiftKey && "shift"].filter(
    Boolean,
  );
  return [...held, press.key.toLowerCase()].join("+");
}

/** The command a key press runs, if any; `typing` says focus is in a text field. */
export function commandFor(press: KeyPress, typing: boolean): Command | undefined {
  const keys = keysOf(press);
  return COMMANDS.find((command) => command.keys === keys && (command.whileTyping || !typing));
}
