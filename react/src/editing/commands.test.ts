import { describe, expect, test, vi } from "vitest";
import { commandFor, isShortcutPress, isUndoOrRedo, keysOf, onEscape, passShortcut } from "./commands";

const press = (key: string, held: { meta?: boolean; ctrl?: boolean; shift?: boolean; alt?: boolean } = {}) => ({
  key,
  metaKey: held.meta ?? false,
  ctrlKey: held.ctrl ?? false,
  shiftKey: held.shift ?? false,
  altKey: held.alt ?? false,
});

describe("shortcuts", () => {
  test("a press is written as the table writes it; Cmd and Ctrl are both mod", () => {
    expect(keysOf(press("S", { meta: true, shift: true }))).toBe("mod+shift+s");
    expect(keysOf(press("z", { ctrl: true }))).toBe("mod+z");
    expect(keysOf(press("z"))).toBe("z");
  });

  test("export works while typing; undo and redo are the text field's own", () => {
    expect(commandFor(press("s", { meta: true }), true)?.keys).toBe("mod+s");
    expect(commandFor(press("z", { meta: true }), false)?.keys).toBe("mod+z");
    expect(commandFor(press("z", { meta: true }), true)).toBeUndefined();
  });

  test("redo is Shift+Cmd+Z or Cmd+Y; a letter alone, or with Alt, is nothing", () => {
    expect(commandFor(press("Z", { meta: true, shift: true }), false)?.keys).toBe("mod+shift+z");
    expect(commandFor(press("y", { ctrl: true }), false)?.keys).toBe("mod+y");
    expect(commandFor(press("s"), false)).toBeUndefined();
    expect(commandFor(press("z", { meta: true, alt: true }), false)).toBeUndefined();
  });

  test("undo and redo are known by every key either goes by, and nothing else is either", () => {
    expect(isUndoOrRedo(press("z", { meta: true }))).toBe(true);
    expect(isUndoOrRedo(press("Z", { ctrl: true, shift: true }))).toBe(true);
    expect(isUndoOrRedo(press("y", { ctrl: true }))).toBe(true);
    expect(isUndoOrRedo(press("z"))).toBe(false);
    expect(isUndoOrRedo(press("s", { meta: true }))).toBe(false);
  });
});

describe("Escape in a control", () => {
  test("runs what it's for and goes no further; any other key goes on to the shortcuts", () => {
    const run = vi.fn();
    const keyed = (key: string) => ({ key, continuePropagation: vi.fn() });
    const escapePress = keyed("Escape");
    onEscape(run)(escapePress);
    expect(run).toHaveBeenCalledOnce();
    expect(escapePress.continuePropagation).not.toHaveBeenCalled();
    const save = keyed("s");
    onEscape(run)(save);
    expect(run).toHaveBeenCalledOnce();
    expect(save.continuePropagation).toHaveBeenCalledOnce();
  });
});

describe("a shortcut pressed on a control", () => {
  test("goes on to the window when Cmd or Ctrl is held from the keyboard, and only then", () => {
    const pressed = (fields: { pointerType: string; metaKey?: boolean; ctrlKey?: boolean }) => ({
      metaKey: false,
      ctrlKey: false,
      ...fields,
      continuePropagation: vi.fn(),
    });
    const modEnter = pressed({ pointerType: "keyboard", ctrlKey: true });
    passShortcut(modEnter);
    expect(isShortcutPress(modEnter)).toBe(true);
    expect(modEnter.continuePropagation).toHaveBeenCalledOnce();
    const enter = pressed({ pointerType: "keyboard" });
    passShortcut(enter);
    expect(isShortcutPress(enter)).toBe(false);
    expect(enter.continuePropagation).not.toHaveBeenCalled();
    // A click with Cmd held is the control's own.
    expect(isShortcutPress(pressed({ pointerType: "mouse", metaKey: true }))).toBe(false);
  });
});
