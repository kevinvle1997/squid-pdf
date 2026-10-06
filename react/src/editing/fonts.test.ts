import { beforeEach, describe, expect, test, vi } from "vitest";
import { deleteFont, ProblemError, putFont, render, upload } from "../api/client";
import type { Document, Render } from "../api/types";
import { aDoc, aFont, aProblem, aSpan } from "../fixtures";
import { createEditor, type Editor } from "./editor";
import { attachDropped, attachFont, detachFont, offersCopy, shownName } from "./fonts";
import { noticeLines } from "./notices";
import { edit, finish, typeInto } from "./typing";

vi.mock(import("../api/client"), async (original) => ({
  ...(await original()),
  render: vi.fn(),
  upload: vi.fn(),
  putFont: vi.fn(),
  deleteFont: vi.fn(),
}));

const NAMED = aFont("Arial", { substitute: "Liberation Sans Regular", glyphs: { H: 722, i: 222 } });
const DOC = aDoc({ spans: [aSpan({ id: "s1", text: "Hi", font: "Arial" })], fonts: [NAMED] });
const JUDGED_AGAIN = aDoc({
  spans: DOC.spans,
  fonts: [aFont("Arial", { attached: true, glyphs: { H: 722, i: 222, Y: 667 } })],
});
const FONT_FILE = new File(["a font"], "arial.ttf");
const MISMATCH = "This isn't the font the document uses. Its letters are a different width.";

let editor: Editor;

beforeEach(() => {
  vi.mocked(render)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  vi.mocked(putFont).mockReset();
  vi.mocked(deleteFont).mockReset();
  vi.mocked(upload).mockReset();
  editor = createEditor(new File(["%PDF-"], "letter.pdf"), DOC, 2);
});

describe("the user's own copy of a font", () => {
  test("once attached, the document the server judged again takes over, and nothing is said", async () => {
    vi.mocked(putFont).mockResolvedValue(JUDGED_AGAIN);

    await attachFont(editor, "Arial", FONT_FILE);

    const state = editor.store.get();
    expect(putFont).toHaveBeenCalledWith(DOC.id, "Arial", FONT_FILE);
    expect(state.layout.fonts.get("Arial")?.attached).toBe(true);
    expect(noticeLines(state.notices, state.drawn)).toEqual([]);
    expect(editor.attached.get("Arial")).toBe(FONT_FILE);
  });

  test("every edited page is drawn again with it, though no edit changed", async () => {
    vi.mocked(putFont).mockResolvedValue(JUDGED_AGAIN);
    edit(editor, "s1", null);
    typeInto(editor, "Hi Y");
    finish(editor, true);
    const asked = vi.mocked(render).mock.calls.length;

    await attachFont(editor, "Arial", FONT_FILE);

    expect(vi.mocked(render).mock.calls.length).toBe(asked + 1);
  });

  test("a copy refused says the server's sentence, warned, until the next change", async () => {
    vi.mocked(putFont).mockRejectedValue(new ProblemError(aProblem(422, MISMATCH)));

    await attachFont(editor, "Arial", FONT_FILE);
    const refused = editor.store.get();
    edit(editor, "s1", null);
    typeInto(editor, "Ho");
    finish(editor, true);
    const changed = editor.store.get();

    expect(noticeLines(refused.notices, refused.drawn)).toEqual([{ tone: "warn", text: MISMATCH }]);
    expect(refused.notices.font?.font).toBe("Arial");
    expect(refused.doc).toBe(DOC);
    expect(editor.attached.has("Arial")).toBe(false);
    expect(changed.notices.font).toBeNull();
  });

  test("removed, the document is judged without it, and the copy is forgotten", async () => {
    vi.mocked(putFont).mockResolvedValue(JUDGED_AGAIN);
    vi.mocked(deleteFont).mockResolvedValue(DOC);
    await attachFont(editor, "Arial", FONT_FILE);

    await detachFont(editor, "Arial");

    expect(deleteFont).toHaveBeenCalledWith(DOC.id, "Arial");
    expect(editor.store.get().layout.fonts.get("Arial")?.attached).toBe(false);
    expect(editor.attached.has("Arial")).toBe(false);
  });

  test("a document opened again gets every copy back, as it does the edits", async () => {
    const reopened: Document = { ...DOC, id: "again" };
    vi.mocked(putFont).mockImplementation(async (docId: string) => ({ ...JUDGED_AGAIN, id: docId }));
    vi.mocked(upload).mockResolvedValue(reopened);
    await attachFont(editor, "Arial", FONT_FILE);
    vi.mocked(putFont).mockClear();

    await editor.reopener.withDocument(async (doc) => {
      if (doc.id === DOC.id) throw new ProblemError(aProblem(404));
      return doc;
    });
    await vi.waitFor(() => expect(editor.store.get().doc.id).toBe("again"));
    await vi.waitFor(() => expect(editor.store.get().layout.fonts.get("Arial")?.attached).toBe(true));

    expect(putFont).toHaveBeenCalledWith("again", "Arial", FONT_FILE);
  });
});

describe("one copy at a time", () => {
  test("a second press while a copy goes in asks nothing more of the server", async () => {
    vi.mocked(putFont).mockResolvedValue(JUDGED_AGAIN);

    await Promise.all([attachFont(editor, "Arial", FONT_FILE), attachFont(editor, "Arial", FONT_FILE)]);

    expect(putFont).toHaveBeenCalledTimes(1);
    expect(editor.store.get().attaching).toBeNull();
  });
});

describe("a file dropped on the field", () => {
  test("that isn't a font still goes, for the server to say what it is", async () => {
    const notAFont = new File(["words"], "notes.txt", { type: "text/plain" });
    vi.mocked(putFont).mockRejectedValue(new ProblemError(aProblem(422, "This file can't be read as a font.")));

    await attachDropped(editor, "Arial", [Promise.resolve(notAFont)]);

    expect(putFont).toHaveBeenCalledWith(DOC.id, "Arial", notAFont);
    expect(editor.store.get().notices.font?.text).toBe("This file can't be read as a font.");
  });

  test("that can't be read is said, not lost", async () => {
    await attachDropped(editor, "Arial", [Promise.reject(new Error("unreadable drop"))]);

    expect(editor.store.get().notices.font?.tone).toBe("warn");
    expect(putFont).not.toHaveBeenCalled();
  });
});

describe("the offer of the user's copy while typing", () => {
  test("is made only when a letter typed is one the font can't draw", () => {
    edit(editor, "s1", null);
    typeInto(editor, "Hi");
    const fits = offersCopy(editor.store.get());
    typeInto(editor, "Hi Y");
    const lacksY = offersCopy(editor.store.get());

    expect(fits).toBe(false);
    expect(lacksY).toBe(true);
  });
});

test("a font is shown by its name, without a trimmed copy's prefix", () => {
  expect(shownName("ABCDEF+Poppins-Regular")).toBe("Poppins-Regular");
  expect(shownName("Arial")).toBe("Arial");
});
