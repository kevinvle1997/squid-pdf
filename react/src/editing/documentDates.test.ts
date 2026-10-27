import { expect, test, vi } from "vitest";
import { renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aFont, aSpan } from "../fixtures";
import { createEditor } from "./editor";
import { editableValueIn } from "./formattedValues";
import { edit } from "./typing";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

// Hawaiian's March: a language with a three letter code alone, learnt after the first edit starts.
const TEXT = "Due 14 Malaki 2026";

test("the first edit reads the document's dates at once, and again once every language is learnt", async () => {
  vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
  const doc = aDoc({ spans: [aSpan({ id: "due", text: TEXT })], fonts: [aFont("Times-Roman")] });
  const editor = createEditor(new File(["%PDF-"], "invoice.pdf"), doc, 2);
  expect(editor.store.get().layout.dates).toBeNull();
  edit(editor, "due", null);
  const first = editor.store.get().layout.dates;
  if (first === null) throw new Error("not read as the edit started");
  expect(editableValueIn(TEXT, { dates: first, at: undefined })).toBeUndefined();

  // The field reads `layout.dates`, so its value is read again as this changes.
  await vi.waitFor(() => expect(editor.store.get().layout.dates).not.toBe(first), { timeout: 20_000 });
  const learnt = editor.store.get().layout.dates;
  if (learnt === null) throw new Error("not read again");
  expect(editableValueIn(TEXT, { dates: learnt, at: undefined })?.kind).toBe("date");
});
