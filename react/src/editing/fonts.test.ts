import { beforeEach, describe, expect, test, vi } from "vitest";
import { deleteFont, ProblemError, putFont, renderStrips, upload } from "../api/client";
import type { Document, Render } from "../api/types";
import { aDoc, aFont, aProblem, aSpan, COPY } from "../fixtures";
import { createEditor, type Editor } from "./editor";
import { attachDropped, attachFont, copiesByName, detachFont, drawnByOf, type FontCopies, offersCopy } from "./fonts";
import { noticeLines } from "./notices";
import { edit, finish, typeInto } from "./typing";
import { fill } from "./words";

vi.mock(import("../api/client"), async (original) => ({
  ...(await original()),
  renderStrips: vi.fn(),
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
  vi.mocked(renderStrips)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  vi.mocked(putFont).mockReset();
  vi.mocked(deleteFont).mockReset();
  vi.mocked(upload).mockReset();
  editor = createEditor(new File(["%PDF-"], "letter.pdf"), DOC, 2);
});

describe("the user's own copy of a font", () => {
  test("once attached, the document the server judged again takes over, with no line under the bar", async () => {
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
    const asked = vi.mocked(renderStrips).mock.calls.length;

    await attachFont(editor, "Arial", FONT_FILE);

    expect(vi.mocked(renderStrips).mock.calls.length).toBe(asked + 1);
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
    expect(refused.said.text).toBe(MISMATCH);
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

  test("while its removal is asked, a document opened again doesn't get it back; refused, it's kept", async () => {
    vi.mocked(putFont).mockResolvedValue(JUDGED_AGAIN);
    await attachFont(editor, "Arial", FONT_FILE);
    let removing = false;
    vi.mocked(deleteFont).mockImplementation(async () => {
      removing = editor.attached.has("Arial");
      throw new ProblemError(aProblem(503));
    });

    await detachFont(editor, "Arial");

    expect(removing).toBe(false);
    expect(editor.attached.get("Arial")).toBe(FONT_FILE);
  });

  test("a copy the document opened again refuses is still said once the others are back", async () => {
    const reopened: Document = { ...DOC, id: "again" };
    vi.mocked(upload).mockResolvedValue(reopened);
    vi.mocked(putFont).mockImplementation(async (docId: string, fontName: string) => {
      if (fontName === "Times") throw new ProblemError(aProblem(422, MISMATCH));
      return { ...JUDGED_AGAIN, id: docId };
    });
    vi.mocked(renderStrips).mockImplementation(async (docId: string) => {
      if (docId === DOC.id) throw new ProblemError(aProblem(404));
      return new Promise<Render>(() => undefined);
    });
    editor.attached.set("Times", FONT_FILE);
    editor.attached.set("Arial", FONT_FILE);
    edit(editor, "s1", null);
    typeInto(editor, "Ho");
    finish(editor, true);

    await vi.waitFor(() => expect(editor.store.get().layout.fonts.get("Arial")?.attached).toBe(true));

    expect(editor.store.get().notices.font).toEqual({ tone: "warn", text: MISMATCH, font: "Times" });
    expect(editor.store.get().said.text).toBe(`${COPY.reopened} ${MISMATCH}`);
    // The edited page is drawn again without the refused copy, on the document opened again.
    await vi.waitFor(() =>
      expect(vi.mocked(renderStrips).mock.calls.filter(([docId]) => docId === "again")).toHaveLength(2),
    );
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
    expect(editor.store.get().said.text).toBe(COPY.reopened);
  });

  test("work that found the document gone runs again only once every copy is back", async () => {
    vi.mocked(upload).mockResolvedValue({ ...DOC, id: "again" });
    vi.mocked(putFont).mockImplementation(async (docId: string) => ({ ...JUDGED_AGAIN, id: docId }));
    editor.attached.set("Arial", FONT_FILE);

    const used = await editor.reopener.withDocument(async (doc) => {
      if (doc.id === DOC.id) throw new ProblemError(aProblem(404));
      return doc;
    });

    expect(used.fonts[0]?.attached).toBe(true);
    expect(editor.store.get().doc).toBe(used);
  });

  test("a document gone again while its copies go back fails the work, not the font", async () => {
    vi.mocked(upload).mockResolvedValue({ ...DOC, id: "again" });
    vi.mocked(putFont).mockRejectedValue(new ProblemError(aProblem(404, "This document is gone.")));
    editor.attached.set("Arial", FONT_FILE);

    const work = editor.reopener.withDocument(async (doc) => {
      if (doc.id === DOC.id) throw new ProblemError(aProblem(404));
      return doc;
    });

    await expect(work).rejects.toMatchObject({ problem: { detail: "This document is gone." } });
    expect(editor.store.get().notices.font).toBeNull();
    expect(editor.attached.get("Arial")).toBe(FONT_FILE);
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

describe("what draws a row of the Fonts list", () => {
  const font = aFont("Arial", { substitute: "Liberation Sans Regular", same_widths: true });
  const row: FontCopies = { shownName: "Arial", names: ["Arial"], font, attached: false };

  test("is said in the server's words, a similar font warned", () => {
    expect(drawnByOf(row, COPY)).toEqual({
      said: fill(COPY.substitute_same_widths, { font: "Liberation Sans Regular" }),
      warn: true,
    });
    expect(drawnByOf({ ...row, attached: true }, COPY)).toEqual({ said: COPY.drawn_in_your_copy, warn: false });
    expect(drawnByOf({ ...row, font: aFont("Arial") }, COPY)).toEqual({ said: COPY.drawn_in_own_copy, warn: false });
  });
});

describe("copies of one font, shown under one name", () => {
  // Two Type3 copies of one font, each named by its object, as the server shows them: one similar font between them.
  const COPIES = [
    aFont("Type3 (5 0 R)", { shown_name: "Drawn-Regular" }),
    aFont("Arial"),
    aFont("Type3 (9 0 R)", { shown_name: "Drawn-Regular", substitute: "Liberation Sans Regular" }),
  ];

  test("share one row of the Fonts list, which says a similar font if any copy has one", () => {
    expect(copiesByName(COPIES).map(({ shownName, names, font }) => [shownName, names, font.name])).toEqual([
      ["Drawn-Regular", ["Type3 (5 0 R)", "Type3 (9 0 R)"], "Type3 (9 0 R)"],
      ["Arial", ["Arial"], "Arial"],
    ]);
  });

  test("take the user's copy together, each put in in turn, and give it up together", async () => {
    const doc = aDoc({ spans: DOC.spans, fonts: COPIES });
    editor = createEditor(new File(["%PDF-"], "letter.pdf"), doc, 2);
    vi.mocked(putFont).mockResolvedValue(doc);
    vi.mocked(deleteFont).mockResolvedValue(doc);

    await attachFont(editor, "Type3 (5 0 R)", FONT_FILE);
    const put = vi.mocked(putFont).mock.calls.map(([, fontName]) => fontName);
    const kept = [...editor.attached.keys()];
    await detachFont(editor, "Type3 (9 0 R)");
    const deleted = vi.mocked(deleteFont).mock.calls.map(([, fontName]) => fontName);

    expect(put).toEqual(["Type3 (5 0 R)", "Type3 (9 0 R)"]);
    expect(kept).toEqual(["Type3 (5 0 R)", "Type3 (9 0 R)"]);
    expect(deleted).toEqual(["Type3 (5 0 R)", "Type3 (9 0 R)"]);
    expect(editor.attached.size).toBe(0);
    expect(editor.store.get().said.text).toBe("Your copy of Drawn-Regular is removed");
  });
});
