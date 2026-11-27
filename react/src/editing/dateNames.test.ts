import { afterEach, expect, test, vi } from "vitest";
import { LEARN_SLICE_MS } from "./constants";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("the languages behind dates are learnt a few steps a task, each slice stopping before a step would run it past its time", async () => {
  vi.resetModules();
  const { learnDateLanguages } = await import("./dateNames");
  let clock = 0;
  const now = vi.spyOn(performance, "now").mockImplementation(() => {
    clock += LEARN_SLICE_MS * 0.4;
    return clock;
  });
  vi.useFakeTimers({ toFake: ["setTimeout"], loopLimit: 100_000 });
  const nextTask = vi.spyOn(globalThis, "setTimeout");
  const learnt = learnDateLanguages();
  await vi.runAllTimersAsync();
  await learnt;

  const tasks = nextTask.mock.invocationCallOrder;
  const readings = now.mock.invocationCallOrder;
  const stepsPerSlice = tasks.map((task, at) => {
    const next = tasks[at + 1] ?? Number.POSITIVE_INFINITY;
    return readings.filter((reading) => reading > task && reading < next).length - 1;
  });
  expect(stepsPerSlice.length).toBeGreaterThan(10);
  expect(new Set(stepsPerSlice.slice(0, -1))).toEqual(new Set([2]));
});
