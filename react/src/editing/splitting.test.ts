import { beforeEach, describe, expect, test, vi } from "vitest";
import { renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { aBoldSentence, aDoc, SENTENCES } from "../fixtures";
import { createEditor, type Editor } from "./editor";
import { entriesOf } from "./history";
import { textIn } from "./project";
import { originalPlaceOf, splitOrJoin, splitRefusalOf } from "./splitting";
import { edit, finish, spanDraftOf, typeIntoRuns } from "./typing";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

// "Text is grouped into spans (one style)", in three runs: a split before "grouped" cuts the first at 8.
const DOC = aDoc(aBoldSentence());
const LINE = ["Text is grouped into ", "spans", " (one style)"];
const BEFORE_GROUPED = "Text is ".length;

let editor: Editor;

beforeEach(() => {
  vi.mocked(renderStrips)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  editor = createEditor(new File(["%PDF-"], "contract.pdf"), DOC, 2);
});

const edits = () => entriesOf(editor.store.get().history).map((entry) => entry.edit);
const draft = () => spanDraftOf(editor.store.get());
const shownLines = () => [...new Set(editor.store.get().layout.lines.values())].map((line) => line.id);

/** Open `spanId`'s field, split it before "grouped", and keep `words` typed in what the field then covers. */
function splitAndType(spanId: string, words: readonly string[]) {
  edit(editor, spanId, null);
  splitOrJoin(editor, BEFORE_GROUPED);
  typeIntoRuns(editor, words);
  finish(editor, true);
}

describe("splitting at the caret", () => {
  test("ends the field there, and an edit kept says where in its span it ends", () => {
    edit(editor, "before", null);
    expect(splitOrJoin(editor, BEFORE_GROUPED)).toBe(BEFORE_GROUPED);
    expect(draft()?.runs).toEqual(["Text is "]);
    expect(editor.store.get().announcement.text).toBe(SENTENCES.split_made);
    typeIntoRuns(editor, ["Text a "]);
    finish(editor, true);
    expect(edits()).toEqual([
      { kind: "replace_runs", runs: [{ span_id: "before", text: "Text a ", end: BEFORE_GROUPED }] },
    ]);
    expect(shownLines()).toEqual(["before", "before@8"]);
  });

  test("lets the text past it be edited as a line of its own, each edit kept", () => {
    splitAndType("before", ["Text a "]);
    edit(editor, "before@8", null);
    expect(draft()?.runs).toEqual(["grouped into ", "spans", " (one style)"]);
    typeIntoRuns(editor, ["sorted into ", "spans", " (one style)"]);
    finish(editor, true);
    expect(edits()).toHaveLength(2);
    expect(edits()[1]).toEqual({
      kind: "replace_runs",
      runs: [
        { span_id: "before", text: "sorted into ", start: BEFORE_GROUPED },
        { span_id: "bold", text: "spans" },
        { span_id: "after", text: " (one style)" },
      ],
    });
  });

  test("is forgotten as the field closes with nothing typed against it", () => {
    edit(editor, "before", null);
    splitOrJoin(editor, BEFORE_GROUPED);
    finish(editor, true);
    expect(edits()).toEqual([]);
    expect(editor.store.get().layout.parts.size).toBe(0);
    expect(shownLines()).toEqual(["before"]);
  });

  test("at the split the field ends at, joins the two again", () => {
    edit(editor, "before", null);
    splitOrJoin(editor, BEFORE_GROUPED);
    expect(splitOrJoin(editor, BEFORE_GROUPED)).toBe(BEFORE_GROUPED);
    expect(draft()?.runs).toEqual(LINE);
    expect(editor.store.get().announcement.text).toBe(SENTENCES.split_joined);
  });

  test("keeps the side holding what's changed, wherever the caret is", () => {
    edit(editor, "before", null);
    typeIntoRuns(editor, [LINE[0] ?? "", "spans", " (one styles)"]);
    expect(splitOrJoin(editor, BEFORE_GROUPED)).toBe(0);
    expect(draft()?.runs).toEqual(["grouped into ", "spans", " (one styles)"]);
  });

  test("among changed words says why it can't be, and leaves them as they were", () => {
    edit(editor, "before", null);
    const words = ["Text was grouped into ", "spans", " (one styles)"];
    typeIntoRuns(editor, words);
    expect(splitOrJoin(editor, "Text was ".length)).toBeNull();
    expect(draft()?.runs).toEqual(words);
    expect(splitRefusalOf(editor.store.get())).toBe(SENTENCES.split_changed);
    typeIntoRuns(editor, LINE);
    expect(splitRefusalOf(editor.store.get())).toBeNull();
  });

  test("at the line's end has nothing to split", () => {
    edit(editor, "before", null);
    expect(splitOrJoin(editor, LINE.join("").length)).toBeNull();
    expect(splitRefusalOf(editor.store.get())).toBe(SENTENCES.split_cant_here);
  });
});

describe("where the caret is in the page's words", () => {
  const original = "Pay by Friday";

  test("is where it is before the change, the field keeping the side the change is on", () => {
    expect(originalPlaceOf(original, original, 4)).toEqual({ at: 4, side: "left" });
    expect(originalPlaceOf(original, "Pay by Monday", 4)).toEqual({ at: 4, side: "right" });
  });

  test("is as far from the end after the change, and nowhere among changed words", () => {
    expect(originalPlaceOf(original, "Paid by Friday", 5)).toEqual({ at: 4, side: "left" });
    expect(originalPlaceOf(original, "Paid by Monday", 8)).toBeNull();
  });
});

describe("splitting text already changed", () => {
  const changed = ["Text is grouped into ", "spans", " (one style!)"];

  beforeEach(() => {
    edit(editor, "before", null);
    typeIntoRuns(editor, changed);
    finish(editor, true);
  });

  test("with nothing more typed, adds no change as the field closes, and the line reads as it did", () => {
    edit(editor, "before", null);
    splitOrJoin(editor, BEFORE_GROUPED);
    finish(editor, true);
    expect(edits()).toHaveLength(1);
    expect(shownLines()).toEqual(["before"]);
  });

  test("with more typed over the side holding the change, keeps both, as that side's one change", () => {
    edit(editor, "before", null);
    splitOrJoin(editor, BEFORE_GROUPED);
    typeIntoRuns(editor, ["sorted into ", "spans", " (one style!)"]);
    finish(editor, true);
    expect(edits()).toHaveLength(2);
    const { reading, layout } = editor.store.get();
    expect(layout.ordered.map((span) => textIn(reading, span)).join("")).toBe("Text is sorted into spans (one style!)");
  });
});
