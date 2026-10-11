import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { ProblemError, pageImage, render, stillThere } from "../api/client";
import type { Render } from "../api/types";
import { A4, aDoc, aProblem } from "../fixtures";
import { IMAGE_RETRY_FIRST_MS } from "./constants";
import { closeEditor, createEditor, type Editor } from "./editor";
import { imageFailed, imageLoaded, retryImages } from "./images";

vi.mock(import("../api/client"), async (original) => ({
  ...(await original()),
  render: vi.fn(),
  stillThere: vi.fn(),
  pageImage: vi.fn(),
}));

const BUSY = "We can't work on files right now. Try again in a few minutes.";

let editor: Editor;

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(render).mockReturnValue(new Promise<Render>(() => undefined));
  vi.mocked(stillThere).mockReset().mockResolvedValue(true);
  vi.mocked(pageImage)
    .mockReset()
    .mockRejectedValue(new ProblemError(aProblem(503, BUSY)));
  editor = createEditor(new File(["%PDF-"], "contract.pdf"), aDoc({ pages: [A4, A4] }), 2);
});

afterEach(() => {
  closeEditor(editor);
  vi.useRealTimers();
});

describe("a page image that fails while the server still has the document", () => {
  test("says why on its page, heard once, and is asked for again after a wait that doubles", async () => {
    await imageFailed(editor, 0);
    expect(editor.store.get().images.get(0)).toEqual({ tries: 0, failed: BUSY });
    expect(editor.store.get().said.text).toBe(BUSY);

    vi.advanceTimersByTime(IMAGE_RETRY_FIRST_MS);
    expect(editor.store.get().images.get(0)?.tries).toBe(1);

    const { said } = editor.store.get();
    await imageFailed(editor, 0);
    vi.advanceTimersByTime(IMAGE_RETRY_FIRST_MS);
    expect(editor.store.get().images.get(0)?.tries).toBe(1);
    vi.advanceTimersByTime(IMAGE_RETRY_FIRST_MS);
    expect(editor.store.get().images.get(0)?.tries).toBe(2);
    // The same reason again isn't said again.
    expect(editor.store.get().said).toBe(said);
  });

  test("loaded at last, it says nothing more and isn't asked for again", async () => {
    await imageFailed(editor, 0);
    imageLoaded(editor, 0);
    vi.advanceTimersByTime(IMAGE_RETRY_FIRST_MS);
    expect(editor.store.get().images.size).toBe(0);
  });

  test("when the connection comes back, every failed page is asked for at once", async () => {
    await imageFailed(editor, 0);
    await imageFailed(editor, 1);
    retryImages(editor);
    expect([...editor.store.get().images.values()].map((image) => image.tries)).toEqual([1, 1]);
  });

  test("a second page failing for the same reason isn't heard twice", async () => {
    await imageFailed(editor, 0);
    const { said } = editor.store.get();
    await imageFailed(editor, 1);
    expect(editor.store.get().images.get(1)?.failed).toBe(BUSY);
    expect(editor.store.get().said).toBe(said);
  });
});
