import { beforeAll, describe, expect, test, vi } from "vitest";
import { renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aFont, aSpan } from "../fixtures";
import { learnEveryLanguage } from "./dateNames";
import { createEditor, type Editor } from "./editor";
import { editableValueIn } from "./formattedValues";
import { edit } from "./typing";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

// Hawaiian's March: a language with a three letter code alone, learnt after the first edit starts.
const TEXT = "Due 14 Malaki 2026";

let editor: Editor;

test("the first edit opens at once: no language is learnt on the spot, and no date is read yet", () => {
  vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
  const doc = aDoc({ spans: [aSpan({ id: "due", text: TEXT })], fonts: [aFont("Times-Roman")] });
  editor = createEditor(new File(["%PDF-"], "invoice.pdf"), doc, 2);
  const asked = vi.spyOn(Intl.DateTimeFormat, "supportedLocalesOf");
  edit(editor, "due", null);
  expect(asked).not.toHaveBeenCalled();
  asked.mockRestore();
  expect(editor.store.get().draft?.spanId).toBe("due");
  expect(editor.store.get().layout.dates).toBeNull();
});

describe("once every language is learnt, in the background", () => {
  beforeAll(() => learnEveryLanguage());

  test("the document's dates are read, so the open field reads its date", () => {
    const { dates } = editor.store.get().layout;
    if (dates === null) throw new Error("not read once learnt");
    expect(editableValueIn(TEXT, { dates, at: undefined })?.kind).toBe("date");
  });
});
