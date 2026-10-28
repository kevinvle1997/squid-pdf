import { beforeEach, describe, expect, test, vi } from "vitest";
import { renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aSpan, PSEUDO_SENTENCES, SENTENCES } from "../fixtures";
import { commandFor } from "./commands";
import { createEditor, type Editor, undo } from "./editor";
import { editAnyway, keepRedaction, redact } from "./redaction";
import { edit, finish, typeInto } from "./typing";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

// Its sentences are the server's, in a language made up for tests, so any the browser writes itself stands out.
const DOC = aDoc({
  sentences: PSEUDO_SENTENCES,
  spans: [aSpan({ id: "name", text: "John Smith" }), aSpan({ id: "other", text: "Acme" })],
});

let editor: Editor;

beforeEach(() => {
  vi.mocked(renderStrips)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  editor = createEditor(new File(["%PDF-"], "contract.pdf"), DOC, 2);
});

const pressed = (keys: Partial<KeyboardEvent>, typing = false) =>
  commandFor({ key: "", metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...keys }, typing);

describe("redacting", () => {
  test("Cmd+Delete on a span redacts the whole of it, says so, and keeps focus there", () => {
    pressed({ key: "Backspace", metaKey: true })?.run(editor, {
      spanId: "name",
      page: null,
      newTextId: null,
      marginNote: false,
    });
    const state = editor.store.get();
    expect(state.reading.edits).toEqual([{ kind: "redact", span_id: "name" }]);
    expect(state.reading.spans.get("name")?.redacted).toBe(true);
    expect(state.announcement.text).toBe("REDACTED “John Smith”.");
    expect(state.focusTo).toEqual({ spanId: "name" });
    expect(renderStrips).toHaveBeenCalledTimes(1);
  });

  test("while typing, Cmd or Ctrl with Backspace or Delete is the field's own, deleting a word or a line", () => {
    edit(editor, "name", null);
    typeInto(editor, "Jane Smith");
    for (const key of ["Backspace", "Delete"]) {
      expect(pressed({ key, ctrlKey: true }, true)).toBeUndefined();
      expect(pressed({ key, metaKey: true }, true)).toBeUndefined();
    }
  });

  test("redacting while typing takes the span typed in, whatever part is selected, and the typed words go", () => {
    edit(editor, "name", null);
    typeInto(editor, "Jane Smith");
    redact(editor, null);
    const state = editor.store.get();
    expect(state.draft).toBeNull();
    expect(state.reading.edits).toEqual([{ kind: "redact", span_id: "name" }]);
  });

  test("a span already redacted isn't redacted again, and a key pressed on no span does nothing", () => {
    redact(editor, "name");
    redact(editor, "name");
    redact(editor, null);
    expect(editor.store.get().reading.edits).toHaveLength(1);
  });

  test("undo takes a redaction off, as it does any edit", () => {
    redact(editor, "name");
    undo(editor);
    expect(editor.store.get().reading.edits).toEqual([]);
  });
});

describe("editing a redacted span", () => {
  test("asks first, in the server's words, and opens nothing yet", () => {
    redact(editor, "name");
    edit(editor, "name", { atPt: 12, selects: "caret" });
    const state = editor.store.get();
    expect(state.unredacting).toEqual({ spanId: "name", press: { atPt: 12, selects: "caret" } });
    expect(state.draft).toBeNull();
    expect(state.announcement.text).toBe(SENTENCES.undo_redaction);
  });

  test("Edit anyway takes the redaction out, and opens the span as it reads without it", () => {
    edit(editor, "name", null);
    typeInto(editor, "Jane Smith");
    finish(editor, true);
    redact(editor, "name");
    edit(editor, "name", null);

    editAnyway(editor);

    const state = editor.store.get();
    expect(state.unredacting).toBeNull();
    expect(state.reading.edits).toEqual([{ kind: "replace", span_id: "name", text: "Jane Smith" }]);
    expect(state.draft).toMatchObject({ spanId: "name", text: "Jane Smith" });
  });

  test("Keep redaction changes nothing, and focus goes back to the span", () => {
    redact(editor, "name");
    edit(editor, "name", null);

    keepRedaction(editor);

    const state = editor.store.get();
    expect(state.unredacting).toBeNull();
    expect(state.reading.edits).toEqual([{ kind: "redact", span_id: "name" }]);
    expect(state.focusTo).toEqual({ spanId: "name" });
  });
});
