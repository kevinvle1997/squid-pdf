import { type Editor, type PressedOn, redo, undo } from "./editor";
import { exportNow } from "./export";
import { openFind, stepFind } from "./find";
import { openNewTextBelow, openNewTextOnBarePage } from "./newText";
import { redact, type Selected } from "./redaction";
import { showShortcuts } from "./shortcuts";

export interface KeyAt extends PressedOn {
  readonly spanId: string | null;
  readonly selected: Selected | null;
}

export interface Command {
  readonly keys: string; // mod is Cmd or Ctrl, either on any platform
  readonly whileTyping: boolean;
  // In a span's field too, over words selected there a redaction can take: the field's own would only delete them.
  readonly whileSelecting: boolean;
  readonly run: (editor: Editor, at: KeyAt) => void;
}

const COMMANDS: readonly Command[] = [
  { keys: "mod+s", whileTyping: true, whileSelecting: false, run: (editor) => void exportNow(editor) },
  // Find across the document, from anywhere: the browser's own find can't read the page's words.
  { keys: "mod+f", whileTyping: true, whileSelecting: false, run: openFind },
  { keys: "mod+g", whileTyping: true, whileSelecting: false, run: (editor) => stepFind(editor, 1) },
  { keys: "mod+shift+g", whileTyping: true, whileSelecting: false, run: (editor) => stepFind(editor, -1) },
  // In a text field, undo and redo are the field's own.
  { keys: "mod+z", whileTyping: false, whileSelecting: false, run: undo },
  { keys: "mod+shift+z", whileTyping: false, whileSelecting: false, run: redo },
  { keys: "mod+y", whileTyping: false, whileSelecting: false, run: redo },
  // A Mac's Delete is Backspace, and a full keyboard's forward Delete works too.
  { keys: "mod+backspace", whileTyping: false, whileSelecting: true, run: redactAt },
  { keys: "mod+delete", whileTyping: false, whileSelecting: true, run: redactAt },
  { keys: "mod+enter", whileTyping: false, whileSelecting: false, run: newTextFrom },
  // The list of these keys: ? is shifted on most keyboards, and not on some.
  { keys: "shift+?", whileTyping: false, whileSelecting: false, run: (editor) => showShortcuts(editor, true) },
  { keys: "?", whileTyping: false, whileSelecting: false, run: (editor) => showShortcuts(editor, true) },
];

function redactAt(editor: Editor, at: KeyAt): void {
  redact(editor, at.spanId, { selected: at.selected });
}

function newTextFrom(editor: Editor, at: KeyAt): void {
  if (at.spanId !== null) {
    openNewTextBelow(editor, at.spanId);
    return;
  }
  const bare = at.page !== null && (editor.store.get().layout.pages.get(at.page) ?? []).length === 0;
  if (bare && at.page !== null) openNewTextOnBarePage(editor, at.page);
}

type KeyPress = Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "shiftKey" | "altKey">;

export function keysOf(press: KeyPress): string {
  const held = [(press.metaKey || press.ctrlKey) && "mod", press.altKey && "alt", press.shiftKey && "shift"].filter(
    Boolean,
  );
  return [...held, press.key.toLowerCase()].join("+");
}

export function commandFor(
  press: KeyPress,
  typing: boolean,
  { selecting = false }: { selecting?: boolean } = {},
): Command | undefined {
  const keys = keysOf(press);
  const runs = (command: Command) => command.whileTyping || !typing || (selecting && command.whileSelecting);
  return COMMANDS.find((command) => command.keys === keys && runs(command));
}

/** In a slip, neither is the window's nor the browser's: its own would undo the typing in the field, out of sight of it. */
export function isUndoOrRedo(press: KeyPress): boolean {
  const run = commandFor(press, false)?.run;
  return run === undo || run === redo;
}

interface ControlPress {
  readonly pointerType: string;
  readonly metaKey: boolean;
  readonly ctrlKey: boolean;
}

export function isShortcutPress(press: ControlPress): boolean {
  return press.pointerType === "keyboard" && (press.metaKey || press.ctrlKey);
}

export function passShortcut(press: ControlPress & { continuePropagation(): void }): void {
  if (isShortcutPress(press)) press.continuePropagation();
}

export function onEscape(run: () => void): (event: { key: string; continuePropagation(): void }) => void {
  return (event) => {
    if (event.key === "Escape") run();
    else event.continuePropagation();
  };
}
