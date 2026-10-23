// Keyboard shortcuts, wherever focus is: one table, read by one listener in EditorShell, so a
// new binding is a row.
import { type Editor, redo, undo } from "./editor";
import { exportNow } from "./export";
import { openFind, stepFind } from "./find";
import { redact } from "./redaction";
import { showShortcuts } from "./shortcuts";

export interface Command {
  readonly keys: string; // as "mod+shift+z"; mod is Cmd or Ctrl, either on any platform
  readonly whileTyping: boolean; // in a text field too, rather than the field's own
  readonly run: (editor: Editor, spanId: string | null) => void; // spanId: the span the key was pressed on
}

const COMMANDS: readonly Command[] = [
  // Export takes the words being typed along.
  { keys: "mod+s", whileTyping: true, run: (editor) => void exportNow(editor) },
  // Find across the document, from anywhere: the browser's own find can't read the page's words.
  { keys: "mod+f", whileTyping: true, run: openFind },
  // The next place found, or the one before, while find is open: as a browser's find goes on.
  { keys: "mod+g", whileTyping: true, run: (editor) => stepFind(editor, 1) },
  { keys: "mod+shift+g", whileTyping: true, run: (editor) => stepFind(editor, -1) },
  // In a text field, undo and redo are the field's own.
  { keys: "mod+z", whileTyping: false, run: undo },
  { keys: "mod+shift+z", whileTyping: false, run: redo },
  { keys: "mod+y", whileTyping: false, run: redo },
  // Redact the span: a Mac's Delete is Backspace, and a full keyboard's forward Delete works too. In a text
  // field they're the field's own, deleting a word or a line, as they do everywhere else; its chip has Redact.
  { keys: "mod+backspace", whileTyping: false, run: redact },
  { keys: "mod+delete", whileTyping: false, run: redact },
  // The list of these keys: ? is shifted on most keyboards, and not on some.
  { keys: "shift+?", whileTyping: false, run: (editor) => showShortcuts(editor, true) },
  { keys: "?", whileTyping: false, run: (editor) => showShortcuts(editor, true) },
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

/** A React Aria key handler where Escape runs `run`: every other key goes on, so the window's shortcuts hear it. */
export function onEscape(run: () => void): (event: { key: string; continuePropagation(): void }) => void {
  return (event) => {
    if (event.key === "Escape") run();
    else event.continuePropagation();
  };
}
