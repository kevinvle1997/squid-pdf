import { afterEach, expect, test, vi } from "vitest";
import { LEARN_SLICE_MS } from "./constants";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("the languages behind dates are learnt a few steps a task, each slice stopping before a step would run it past its time", async () => {
  // Learnt afresh: what an earlier test learnt is kept by the module.
  vi.resetModules();
  const { learnDateLanguages } = await import("./dateNames");
  // Every step takes two fifths of a slice, as the clock reads: two fit, and a third would run past.
  let clock = 0;
  const now = vi.spyOn(performance, "now").mockImplementation(() => {
    clock += LEARN_SLICE_MS * 0.4;
    return clock;
  });
  // Each wait for the next task ends at once: the steps are counted here, not timed.
  vi.useFakeTimers({ toFake: ["setTimeout"], loopLimit: 100_000 });
  const nextTask = vi.spyOn(globalThis, "setTimeout");
  const learnt = learnDateLanguages();
  await vi.runAllTimersAsync();
  await learnt;

  // A slice reads the clock as it starts and after each step: its steps are its readings less one.
  const tasks = nextTask.mock.invocationCallOrder;
  const readings = now.mock.invocationCallOrder;
  const stepsPerSlice = tasks.map((task, at) => {
    const next = tasks[at + 1] ?? Number.POSITIVE_INFINITY;
    return readings.filter((reading) => reading > task && reading < next).length - 1;
  });
  expect(stepsPerSlice.length).toBeGreaterThan(10);
  // The last slice may end early, with nothing left to learn.
  expect(new Set(stepsPerSlice.slice(0, -1))).toEqual(new Set([2]));
});
