import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { createNotes } from "./notes";

// Only its identity matters here: which mark a note is over.
const markA = {} as Element;
const markB = {} as Element;

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("a page's note", () => {
  test("a hover shows it after the delay; leaving first, never", () => {
    const notes = createNotes(250);
    notes.show("a", markA, "hover");
    expect(notes.store.get().shown).toBeNull();
    vi.advanceTimersByTime(250);
    expect(notes.store.get().shown).toEqual({ spanId: "a", anchor: markA, via: "hover" });

    notes.hide("a");
    notes.show("b", markB, "hover");
    notes.hide("b");
    vi.advanceTimersByTime(1000);
    expect(notes.store.get().shown).toBeNull();
  });

  test("focus and a tap show it at once; one span's leaving doesn't close another's", () => {
    const notes = createNotes(250);
    notes.show("a", markA, "focus");
    expect(notes.store.get().shown?.spanId).toBe("a");
    notes.hide("b");
    expect(notes.store.get().shown?.spanId).toBe("a");
    notes.show("b", markB, "tap");
    expect(notes.store.get().shown?.spanId).toBe("b");
  });

  test("a click's focus doesn't take over the note its hover showed", () => {
    const notes = createNotes(250);
    notes.show("a", markA, "hover");
    vi.advanceTimersByTime(250);
    notes.show("a", markA, "focus");
    expect(notes.store.get().shown?.via).toBe("hover");
  });

  test("going from note to note, the next shows at once, and the last stays while it closes", () => {
    const notes = createNotes(250);
    notes.show("a", markA, "focus");
    notes.hide("a");
    expect(notes.store.get()).toEqual({ shown: null, last: { spanId: "a", anchor: markA, via: "focus" } });
    notes.show("b", markB, "hover");
    expect(notes.store.get().shown?.spanId).toBe("b");
  });
});
