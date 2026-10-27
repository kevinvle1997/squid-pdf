// Keyboard shortcuts, wherever focus is: one table, read by one listener in EditorShell, so a
// new binding is a row.
import { type Editor, type PressedOn, redo, undo } from "./editor";
import { exportNow } from "./export";
import { openFind, stepFind } from "./find";
import { writeBelow, writeOnBarePage } from "./newText";
import { redact } from "./redaction";
import { showShortcuts } from "./shortcuts";

/** Where a key was pressed: on a span's mark, by its id, or on a page's own control, by its page. */
export interface KeyAt extends PressedOn {
  readonly spanId: string | null;
}

export interface Command {
  readonly keys: string; // as "mod+shift+z"; mod is Cmd or Ctrl, either on any platform
  readonly whileTyping: boolean; // in a text field too, rather than the field's own
  readonly run: (editor: Editor, at: KeyAt) => void;
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
  { keys: "mod+backspace", whileTyping: false, run: (editor, at) => redact(editor, at.spanId) },
  { keys: "mod+delete", whileTyping: false, run: (editor, at) => redact(editor, at.spanId) },
  // New text on the line below the span, or on a page with no text, at its top left. In a field, Enter is the field's.
  { keys: "mod+enter", whileTyping: false, run: newTextFrom },
  // The list of these keys: ? is shifted on most keyboards, and not on some.
  { keys: "shift+?", whileTyping: false, run: (editor) => showShortcuts(editor, true) },
  { keys: "?", whileTyping: false, run: (editor) => showShortcuts(editor, true) },
];

/** New text from the keyboard: below the span the key was pressed on, or at the top of a page with no text. */
function newTextFrom(editor: Editor, at: KeyAt): void {
  if (at.spanId !== null) {
    writeBelow(editor, at.spanId);
    return;
  }
  const bare = at.page !== null && (editor.store.get().layout.pages.get(at.page) ?? []).length === 0;
  if (bare && at.page !== null) writeOnBarePage(editor, at.page);
}

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

/**
 * Whether a key press is undo or redo, as the window's shortcuts have them. In a slip, neither is the window's, nor the
 * browser's: its own would undo the typing in the field, out of sight of it.
 */
export function isUndoOrRedo(press: KeyPress): boolean {
  const run = commandFor(press, false)?.run;
  return run === undo || run === redo;
}

/** A control's press, as React Aria says it: by which pointer, and the keys held with it. */
interface ControlPress {
  readonly pointerType: string;
  readonly metaKey: boolean;
  readonly ctrlKey: boolean;
}

/** Whether a press on a control is a key held with Cmd or Ctrl: a window's shortcut (Cmd+Enter), not the control's. */
export function isShortcutPress(press: ControlPress): boolean {
  return press.pointerType === "keyboard" && (press.metaKey || press.ctrlKey);
}

/** A React Aria `onPressStart` that lets a shortcut's key go on to the window, which the control would keep. */
export function passShortcut(press: ControlPress & { continuePropagation(): void }): void {
  if (isShortcutPress(press)) press.continuePropagation();
}

/** A React Aria key handler where Escape runs `run`: every other key goes on, so the window's shortcuts hear it. */
export function onEscape(run: () => void): (event: { key: string; continuePropagation(): void }) => void {
  return (event) => {
    if (event.key === "Escape") run();
    else event.continuePropagation();
  };
}
