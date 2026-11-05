import { afterEach, expect, test, vi } from "vitest";
import { EMPTY_HISTORY } from "./history";
import { forgetAtUntil, type Persistence, stopForgetting } from "./workPersistence";

afterEach(() => vi.useRealTimers());

/** What's persisted of an open document, the server keeping it another `ms`. */
function persistedFor(ms: number): Persistence {
  return { file: true, history: EMPTY_HISTORY, until: Date.now() + ms, forgetting: null };
}

test("what's persisted here is forgotten as the server's hour runs out, to be persisted afresh from then on", () => {
  vi.useFakeTimers();
  const persistence = persistedFor(1000);
  forgetAtUntil(persistence);
  vi.advanceTimersByTime(999);
  expect(persistence.file).toBe(true);
  vi.advanceTimersByTime(1);
  expect(persistence).toEqual({ file: false, history: null, until: persistence.until, forgetting: null });
});

test("the hour moving on moves the forgetting with it, and a closed editor forgets nothing", () => {
  vi.useFakeTimers();
  const persistence = persistedFor(1000);
  forgetAtUntil(persistence);
  persistence.until += 1000;
  forgetAtUntil(persistence);
  vi.advanceTimersByTime(1500);
  expect(persistence.file).toBe(true);
  stopForgetting(persistence);
  vi.advanceTimersByTime(1000);
  expect(persistence.file).toBe(true);
  expect(persistence.forgetting).toBeNull();
});
