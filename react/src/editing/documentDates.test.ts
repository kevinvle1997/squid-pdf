import { beforeAll, describe, expect, test, vi } from "vitest";
import { renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aFont, aSpan } from "../fixtures";
import { learnDateLanguages } from "./dateNames";
import { createEditor, type Editor } from "./editor";
import { editableValueIn } from "./formattedValues";
import { edit, spanDraftOf } from "./typing";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

// Welsh's March: a month no other date language names, so read only once Welsh is learnt.
const TEXT = "Due 14 Mawrth 2026";

let editor: Editor;

test("the first edit opens at once: no language is learnt on the spot, and no date is read yet", () => {
  vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
  const doc = aDoc({ spans: [aSpan({ id: "due", text: TEXT })], fonts: [aFont("Times-Roman")] });
  editor = createEditor(new File(["%PDF-"], "invoice.pdf"), doc, 2);
  const asked = vi.spyOn(Intl.DateTimeFormat, "supportedLocalesOf");
  edit(editor, "due", null);
  expect(asked).not.toHaveBeenCalled();
  asked.mockRestore();
  expect(spanDraftOf(editor.store.get())?.spanId).toBe("due");
  expect(editor.store.get().dates).toBeNull();
});

describe("once the date languages are learnt, in the background", () => {
  beforeAll(() => learnDateLanguages());

  test("the document's dates are read, so the open field reads its date", () => {
    const { dates } = editor.store.get();
    if (dates === null) throw new Error("not read once learnt");
    expect(editableValueIn(TEXT, { dates, at: undefined, held: null })?.kind).toBe("date");
  });
});
