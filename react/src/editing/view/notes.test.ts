import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { NOTE_CLOSE_DELAY_MS } from "../constants";
import { createNotes, isShowing } from "./notes";

// Which mark a note is over, and whether that mark is still drawn.
const markA = { isConnected: true } as Element;
const markB = { isConnected: true } as Element;

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
    expect(notes.store.get().shown).toEqual({ markId: "a", anchor: markA, via: "hover" });

    notes.hide("a");
    notes.show("b", markB, "hover");
    notes.hide("b");
    vi.advanceTimersByTime(1000);
    expect(notes.store.get().shown).toBeNull();
  });

  test("focus and a tap show it at once; one mark's leaving doesn't close another's", () => {
    const notes = createNotes(250);
    notes.show("a", markA, "focus");
    expect(notes.store.get().shown?.markId).toBe("a");
    notes.hide("b");
    expect(notes.store.get().shown?.markId).toBe("a");
    notes.show("b", markB, "tap");
    expect(notes.store.get().shown?.markId).toBe("b");
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
    expect(notes.store.get()).toEqual({ shown: null, last: { markId: "a", anchor: markA, via: "focus" } });
    notes.show("b", markB, "hover");
    expect(notes.store.get().shown?.markId).toBe("b");
  });

  test("leaving its mark, it closes after the delay, unless the pointer comes onto it; leaving that closes it too", () => {
    const notes = createNotes(250);
    notes.show("a", markA, "hover");
    vi.advanceTimersByTime(250);
    notes.hideSoon("a");
    notes.keep("a");
    vi.advanceTimersByTime(NOTE_CLOSE_DELAY_MS);
    expect(notes.store.get().shown?.markId).toBe("a");

    notes.hideSoon("a");
    vi.advanceTimersByTime(NOTE_CLOSE_DELAY_MS - 1);
    expect(notes.store.get().shown?.markId).toBe("a");
    vi.advanceTimersByTime(1);
    expect(notes.store.get().shown).toBeNull();
  });

  test("a note closing as the pointer left doesn't close the one shown after it", () => {
    const notes = createNotes(250);
    notes.show("a", markA, "hover");
    vi.advanceTimersByTime(250);
    notes.hideSoon("a");
    notes.show("b", markB, "hover");
    notes.hide("b");
    notes.show("a", markA, "focus");
    vi.advanceTimersByTime(NOTE_CLOSE_DELAY_MS);
    expect(notes.store.get().shown?.markId).toBe("a");
  });

  test("one focus holds comes back once another mark's a hover showed closes", () => {
    const notes = createNotes(250);
    notes.show("a", markA, "focus");
    notes.show("b", markB, "hover");
    expect(notes.store.get().shown?.markId).toBe("b");
    notes.hideSoon("b");
    vi.advanceTimersByTime(NOTE_CLOSE_DELAY_MS);
    expect(notes.store.get().shown).toEqual({ markId: "a", anchor: markA, via: "focus" });
  });

  test("one focus holds whose mark is gone isn't shown again once another mark's closes: it's let go", () => {
    const notes = createNotes(250);
    const gone = { isConnected: false } as Element;
    notes.show("a", gone, "focus");
    notes.show("b", markB, "hover");
    notes.hideSoon("b");
    vi.advanceTimersByTime(NOTE_CLOSE_DELAY_MS);
    expect(notes.store.get().shown).toBeNull();
    notes.show("b", markB, "hover");
    notes.hideSoon("b");
    vi.advanceTimersByTime(NOTE_CLOSE_DELAY_MS);
    expect(notes.store.get().shown).toBeNull();
  });

  test("one focus showed stays as the pointer leaves, as focus's, a hover over it or not, until it's closed", () => {
    const notes = createNotes(250);
    notes.show("a", markA, "focus");
    notes.hideSoon("a");
    vi.advanceTimersByTime(NOTE_CLOSE_DELAY_MS);
    expect(notes.store.get().shown).toEqual({ markId: "a", anchor: markA, via: "focus" });

    notes.show("a", markA, "hover");
    notes.hideSoon("a");
    vi.advanceTimersByTime(NOTE_CLOSE_DELAY_MS);
    expect(notes.store.get().shown?.via).toBe("focus");

    // Closed by Escape, or focus leaving: a hover's note after it closes as the pointer leaves.
    notes.hide("a");
    notes.show("a", markA, "hover");
    notes.hideSoon("a");
    vi.advanceTimersByTime(NOTE_CLOSE_DELAY_MS);
    expect(notes.store.get().shown).toBeNull();
  });

  test("focus coming to a mark whose note a hover showed holds it as the pointer leaves", () => {
    const notes = createNotes(250);
    notes.show("a", markA, "hover");
    vi.advanceTimersByTime(250);
    notes.show("a", markA, "focus");
    notes.hideSoon("a");
    vi.advanceTimersByTime(NOTE_CLOSE_DELAY_MS);
    expect(notes.store.get().shown).toEqual({ markId: "a", anchor: markA, via: "focus" });
  });
});

describe("whether a note shows", () => {
  test("one focus showed shows only while focus is the keyboard's; a hover's or a tap's shows either way", () => {
    expect(isShowing(null, true)).toBe(false);
    expect(isShowing({ markId: "a", anchor: markA, via: "focus" }, true)).toBe(true);
    expect(isShowing({ markId: "a", anchor: markA, via: "focus" }, false)).toBe(false);
    expect(isShowing({ markId: "a", anchor: markA, via: "hover" }, false)).toBe(true);
    expect(isShowing({ markId: "a", anchor: markA, via: "tap" }, false)).toBe(true);
  });
});
