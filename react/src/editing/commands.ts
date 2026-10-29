// Keyboard shortcuts, wherever focus is: one table, read by one listener in EditorShell, so a
// new binding is a row.
import { type Editor, type PressedOn, redo, undo } from "./editor";
import { exportNow } from "./export";
import { openFind, stepFind } from "./find";
import { openNewTextBelow, openNewTextOnBarePage } from "./newText";
import { redact, type Selected } from "./redaction";
import { showShortcuts } from "./shortcuts";

/** Where a key was pressed (`PressedOn`), the span's mark it was on, by its id, and what its field had selected. */
export interface KeyAt extends PressedOn {
  readonly spanId: string | null;
  readonly selected: Selected | null; // in the span's field typed in, when a key was pressed there over words
}

export interface Command {
  readonly keys: string; // as "mod+shift+z"; mod is Cmd or Ctrl, either on any platform
  readonly whileTyping: boolean; // in a text field too, rather than the field's own
  // In a span's field too, over words selected there a redaction can take: the field's own would only delete them.
  readonly whileSelecting: boolean;
  readonly run: (editor: Editor, at: KeyAt) => void;
}

const COMMANDS: readonly Command[] = [
  // Export takes the words being typed along.
  { keys: "mod+s", whileTyping: true, whileSelecting: false, run: (editor) => void exportNow(editor) },
  // Find across the document, from anywhere: the browser's own find can't read the page's words.
  { keys: "mod+f", whileTyping: true, whileSelecting: false, run: openFind },
  // The next place found, or the one before, while find is open: as a browser's find goes on.
  { keys: "mod+g", whileTyping: true, whileSelecting: false, run: (editor) => stepFind(editor, 1) },
  { keys: "mod+shift+g", whileTyping: true, whileSelecting: false, run: (editor) => stepFind(editor, -1) },
  // In a text field, undo and redo are the field's own.
  { keys: "mod+z", whileTyping: false, whileSelecting: false, run: undo },
  { keys: "mod+shift+z", whileTyping: false, whileSelecting: false, run: redo },
  { keys: "mod+y", whileTyping: false, whileSelecting: false, run: redo },
  // Redact: a Mac's Delete is Backspace, and a full keyboard's forward Delete works too. In a span's field they take
  // the words selected; else they're the field's own, deleting a word or a line, as they do everywhere else.
  { keys: "mod+backspace", whileTyping: false, whileSelecting: true, run: redactAt },
  { keys: "mod+delete", whileTyping: false, whileSelecting: true, run: redactAt },
  // New text on the line below the span, or on a page with no text, at its top left. In a field, Enter is the field's.
  { keys: "mod+enter", whileTyping: false, whileSelecting: false, run: newTextFrom },
  // The list of these keys: ? is shifted on most keyboards, and not on some.
  { keys: "shift+?", whileTyping: false, whileSelecting: false, run: (editor) => showShortcuts(editor, true) },
  { keys: "?", whileTyping: false, whileSelecting: false, run: (editor) => showShortcuts(editor, true) },
];

/** Redaction from the keyboard: the words selected in the span's field, or the span the key was pressed on. */
function redactAt(editor: Editor, at: KeyAt): void {
  redact(editor, at.spanId, { selected: at.selected });
}

/** New text from the keyboard: below the span the key was pressed on, or at the top of a page with no text. */
function newTextFrom(editor: Editor, at: KeyAt): void {
  if (at.spanId !== null) {
    openNewTextBelow(editor, at.spanId);
    return;
  }
  const bare = at.page !== null && (editor.store.get().layout.pages.get(at.page) ?? []).length === 0;
  if (bare && at.page !== null) openNewTextOnBarePage(editor, at.page);
}

type KeyPress = Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "shiftKey" | "altKey">;

/** A key press as the table writes one: "mod+shift+z". */
export function keysOf(press: KeyPress): string {
  const held = [(press.metaKey || press.ctrlKey) && "mod", press.altKey && "alt", press.shiftKey && "shift"].filter(
    Boolean,
  );
  return [...held, press.key.toLowerCase()].join("+");
}

/**
 * The command a key press runs, if any; `typing` says focus is in a text field, and `selecting` that it's a span's,
 * over words a redaction can take.
 */
export function commandFor(
  press: KeyPress,
  typing: boolean,
  { selecting = false }: { selecting?: boolean } = {},
): Command | undefined {
  const keys = keysOf(press);
  const runs = (command: Command) => command.whileTyping || !typing || (selecting && command.whileSelecting);
  return COMMANDS.find((command) => command.keys === keys && runs(command));
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
