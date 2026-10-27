import { beforeEach, describe, expect, test, vi } from "vitest";
import { exportPdf, ProblemError, renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { A4, aDoc, aFileNotice, aFont, aProblem, aSpan, PSEUDO_SENTENCES, SENTENCES } from "../fixtures";
import { createEditor, type Editor, undo } from "./editor";
import { exportName, exportNow, partName, splitAfter, splitPages, unexported } from "./export";
import { plain, warn } from "./notices";
import { deletePage } from "./pageDeletion";
import { edit, finish, typeInto } from "./typing";

vi.mock(import("../api/client"), async (original) => ({
  ...(await original()),
  renderStrips: vi.fn(),
  exportPdf: vi.fn(),
  upload: vi.fn(),
  stillThere: vi.fn(),
}));

// Its sentences are the server's, in a language made up for tests, so any the browser writes itself stands out.
const DOC = aDoc({
  sentences: PSEUDO_SENTENCES,
  spans: [
    aSpan({ id: "own", font: "Kept" }),
    aSpan({
      id: "substituted",
      font: "Named",
      fidelity: "substitute",
      substitute: "Liberation Serif Regular",
      in_file: false,
    }),
  ],
  fonts: [aFont("Kept"), aFont("Named", { substitute: "Liberation Serif Regular" })],
});
const FILE = new File(["%PDF-"], "contract.pdf");

let editor: Editor;

beforeEach(() => {
  vi.mocked(renderStrips)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  vi.mocked(exportPdf).mockReset();
  editor = createEditor(FILE, DOC, 2);
});

const typed = (spanId: string, text: string) => {
  edit(editor, spanId, null);
  typeInto(editor, text);
  finish(editor, true);
};

describe("export", () => {
  let names: string[];

  beforeEach(() => {
    names = [];
    // Each download is a link clicked: its name is what the browser saves the file as.
    const link = {
      click: vi.fn(function (this: { download: string }) {
        names.push(this.download);
      }),
      download: "",
    };
    vi.stubGlobal("document", { createElement: () => link });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:", revokeObjectURL: vi.fn() });
  });

  test("an edit still being typed goes in first", async () => {
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [], notices: [] });
    edit(editor, "own", null);
    typeInto(editor, "typed");
    await exportNow(editor);
    expect(exportPdf).toHaveBeenCalledWith("doc", [{ kind: "replace", span_id: "own", text: "typed" }], {
      pages: undefined,
    });
    // Named once the typing is in: it's the edit that marks the file edited.
    expect(editor.store.get().notices.export).toEqual([plain("DOWNLOADED contract, edited.pdf.")]);
    expect(editor.store.get().exporting).toBe(false);
  });

  test("a reading persisted before the download's sentence was sent still downloads, and says nothing of it", async () => {
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [], notices: [] });
    const stale = structuredClone(DOC);
    Reflect.deleteProperty(stale.sentences, "downloaded");
    editor = createEditor(FILE, stale, 2);
    typed("own", "typed");
    await exportNow(editor);
    expect(names).toEqual(["contract, edited.pdf"]);
    expect(editor.store.get().notices.export).toEqual([]);
  });

  test("an export started while typing keeps the words and starts in the same update", async () => {
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [], notices: [] });
    edit(editor, "own", null);
    typeInto(editor, "typed");
    const heard: { exporting: boolean; typing: boolean }[] = [];
    const stop = editor.store.subscribe(() => {
      const { exporting, draft } = editor.store.get();
      heard.push({ exporting, typing: draft !== null });
    });
    const exporting = exportNow(editor);
    stop();
    await exporting;
    expect(heard).toEqual([{ exporting: true, typing: false }]);
  });

  test("edits are unexported from the first one until an export takes them, and again after an undo", async () => {
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [], notices: [] });
    expect(unexported(editor.store.get())).toBe(false);
    typed("own", "now");
    expect(unexported(editor.store.get())).toBe(true);
    await exportNow(editor);
    expect(unexported(editor.store.get())).toBe(false);
    undo(editor);
    expect(unexported(editor.store.get())).toBe(true);
  });

  test("words still being typed are unexported too, but a field opened and left as it was isn't", () => {
    edit(editor, "own", null);
    expect(unexported(editor.store.get())).toBe(false);
    typeInto(editor, "now");
    expect(unexported(editor.store.get())).toBe(true);
  });

  test("an export that failed leaves its edits unexported", async () => {
    vi.mocked(exportPdf).mockRejectedValue(new ProblemError(aProblem(503, "Try again.")));
    typed("own", "now");
    await exportNow(editor);
    expect(unexported(editor.store.get())).toBe(true);
  });

  test("edits the server left out are said, as a warning, and so is everything else saving did otherwise", async () => {
    const tags = "Splitting the file dropped its tags, so a screen reader reads it with less structure.";
    const signed = "Every signature in this file was removed, since an edit breaks it.";
    const notices = [aFileNotice(tags), aFileNotice(signed)];
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [0], notices });
    typed("own", "now");
    await exportNow(editor);
    expect(editor.store.get().notices.export).toEqual([warn(SENTENCES.export_left_out), warn(tags), warn(signed)]);
    expect(editor.store.get().announcement.text).toBe(`${SENTENCES.export_left_out} ${tags} ${signed}`);
  });

  test("a bug in export is said and logged, not thrown, and export can be tried again", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(exportPdf).mockRejectedValue(new TypeError("undefined is not a function"));
    await exportNow(editor);
    expect(editor.store.get().notices.export.map((notice) => notice.tone)).toEqual(["warn"]);
    expect(logged).toHaveBeenCalledWith(new TypeError("undefined is not a function"));
    expect(editor.store.get().exporting).toBe(false);
  });

  test("a failure is said in the server's words, and export can be tried again", async () => {
    const problem = aProblem(422, "Couldn't remove it, so nothing was downloaded.");
    vi.mocked(exportPdf).mockRejectedValue(new ProblemError(problem));
    await exportNow(editor);
    expect(editor.store.get().notices.export).toEqual([warn(problem.detail)]);
    expect(editor.store.get().exporting).toBe(false);
  });

  test("a split downloads two files, every edit in each, named for the pages each holds", async () => {
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [], notices: [] });
    const threePages = createEditor(FILE, { ...DOC, pages: [A4, A4, A4] }, 2);
    edit(threePages, "own", null);
    typeInto(threePages, "now");
    finish(threePages, true);
    await splitAfter(threePages, 0);
    const edits = [{ kind: "replace", span_id: "own", text: "now" }];
    expect(vi.mocked(exportPdf).mock.calls).toEqual([
      ["doc", edits, { pages: [0] }],
      ["doc", edits, { pages: [1, 2] }],
    ]);
    expect(names).toEqual(["contract, page 1.pdf", "contract, pages 2–3.pdf"]);
    expect(threePages.store.get().notices.export).toEqual([
      plain("DOWNLOADED contract, page 1.pdf."),
      plain("DOWNLOADED contract, pages 2–3.pdf."),
    ]);
    expect(unexported(threePages.store.get())).toBe(false);
  });

  test("a split says once what both files say, and stops at the first file that fails", async () => {
    const tags = "The tags that tell screen readers how to read this file went with the pages left out.";
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [], notices: [aFileNotice(tags)] });
    const twoPages = createEditor(FILE, { ...DOC, pages: [A4, A4] }, 2);
    await splitAfter(twoPages, 0);
    expect(twoPages.store.get().notices.export).toEqual([warn(tags)]);

    vi.mocked(exportPdf)
      .mockReset()
      .mockRejectedValue(new ProblemError(aProblem(503, "Try again.")));
    await splitAfter(twoPages, 0);
    expect(exportPdf).toHaveBeenCalledTimes(1);
    expect(twoPages.store.get().notices.export).toEqual([warn("Try again.")]);
  });
});

describe("a split's parts", () => {
  test("are the pages up to the split, and the rest", () => {
    expect(splitPages([0, 1, 2, 3], 1)).toEqual([
      [0, 1],
      [2, 3],
    ]);
  });

  test("after a delete, are the pages shown either side: the deleted page in neither, as export asks", () => {
    expect(splitPages([0, 1, 3], 1)).toEqual([[0, 1], [3]]);
  });

  test("after a delete, are sent with the delete, the deleted page in neither", async () => {
    vi.mocked(exportPdf).mockResolvedValue({ pdf: new Blob(), skipped: [], notices: [] });
    const four = createEditor(FILE, aDoc({ pages: [A4, A4, A4, A4] }), 2);
    deletePage(four, 2);
    await splitAfter(four, 1);
    const asked = vi.mocked(exportPdf).mock.calls.map(([, edits, options]) => [edits, options?.pages]);
    const deleted = [{ kind: "delete_page", page: 2 }];
    expect(asked).toEqual([
      [deleted, [0, 1]],
      [deleted, [3]],
    ]);
  });

  test("are named from the upload, with the pages they hold counted from one", () => {
    expect(partName("contract.pdf", [0, 1, 2])).toBe("contract, pages 1–3.pdf");
    expect(partName("Contract.PDF", [3])).toBe("Contract, page 4.pdf");
  });
});

describe("the whole document's file name", () => {
  test("is the upload's, marked edited once it is, so it isn't taken for the original", () => {
    expect(exportName("contract.pdf", { edited: true })).toBe("contract, edited.pdf");
    expect(exportName("Contract.PDF", { edited: true })).toBe("Contract, edited.pdf");
    expect(exportName("contract.pdf", { edited: false })).toBe("contract.pdf");
  });
});
