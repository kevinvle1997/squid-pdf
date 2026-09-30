import { expect, test } from "vitest";
import { createStore, shallowEqual } from "./store";

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

test("a selection of several parts is the same while each part is the same object", () => {
  const edits = { spans: [] };
  expect(shallowEqual({ edits, src: "/p/1" }, { edits, src: "/p/1" })).toBe(true);
  expect(shallowEqual({ edits, src: "/p/1" }, { edits: { spans: [] }, src: "/p/1" })).toBe(false);
  expect(shallowEqual<{ src: string; extra?: number }>({ src: "/p/1" }, { src: "/p/1", extra: 1 })).toBe(false);
});
