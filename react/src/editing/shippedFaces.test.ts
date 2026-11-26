import { expect, test, vi } from "vitest";
import { fontList, ProblemError } from "../api/client";
import { aDoc, aProblem } from "../fixtures";
import { createEditor } from "./editor";
import { loadShippedFaces } from "./shippedFaces";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), fontList: vi.fn() }));

const BUSY = "We can't work on files right now. Try again in a few minutes.";

test("an editor asks for the font list once: one that failed says why, and isn't asked for again", async () => {
  vi.mocked(fontList).mockRejectedValue(new ProblemError(aProblem(503, BUSY)));
  const editor = createEditor(new File(["%PDF-"], "contract.pdf"), aDoc({ build: "failing" }), 2);
  await loadShippedFaces(editor);
  await loadShippedFaces(editor);
  expect(fontList).toHaveBeenCalledOnce();
  expect(editor.store.get().shippedFaces).toEqual({ handwriting: [], failed: BUSY });
});
