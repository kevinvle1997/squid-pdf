import { expect, test } from "vitest";
import { createStore } from "./store";

test("a change is there the moment it's made, and every subscriber hears of it until it stops", () => {
  const store = createStore({ draft: "", count: 0 });
  const heard: string[] = [];
  const stop = store.subscribe(() => heard.push(store.get().draft));
  store.set({ draft: "a" });
  expect(store.get()).toEqual({ draft: "a", count: 0 });
  stop();
  store.set({ draft: "ab" });
  expect(heard).toEqual(["a"]);
});
