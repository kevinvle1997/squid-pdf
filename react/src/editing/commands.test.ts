import { describe, expect, test } from "vitest";
import { commandFor, keysOf } from "./commands";

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
});
