import { beforeEach, describe, expect, test, vi } from "vitest";
import { renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aFont, aSpan, COPY, halfWideGlyphs } from "../fixtures";
import { createEditor, type Editor } from "./editor";
import { quietFont } from "./quiet";
import { edit, enter, fieldFocused, finish, stepWayOut, troublesIn, typeInto } from "./typing";
import { fill } from "./words";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

const DOC = aDoc({
  spans: [
    aSpan({ id: "own", font: "Kept" }),
    aSpan({ id: "substituted", font: "Named", fidelity: "substitute", substitute: "Liberation Serif Regular" }),
  ],
  fonts: [aFont("Kept"), aFont("Named", { substitute: "Liberation Serif Regular" })],
});
const FILE = new File(["%PDF-"], "contract.pdf");

let editor: Editor;

beforeEach(() => {
  vi.mocked(renderStrips)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  editor = createEditor(FILE, DOC, 2);
});

const typed = (spanId: string, text: string) => {
  edit(editor, spanId, null);
  typeInto(editor, text);
  finish(editor, true);
};

describe("an edit", () => {
  test("as its field takes focus, what an edit there warns of is heard, until its font is quieted", () => {
    // Its font has every letter it reads, so the warning is all there is to hear.
    const glyphs = halfWideGlyphs("was similar");
    const similar = aSpan({
      id: "similar",
      font: "Named",
      fidelity: "substitute",
      substitute: "Liberation Serif Regular",
    });
    const fonts = [aFont("Named", { substitute: "Liberation Serif Regular", glyphs })];
    editor = createEditor(FILE, aDoc({ spans: [similar], fonts }), 2);
    edit(editor, "similar", null);
    fieldFocused(editor);
    expect(editor.store.get().said.text).toBe(fill(COPY.substitute, { font: "Liberation Serif Regular" }));
    finish(editor, false);
    quietFont(editor, "Named", { quiet: true });
    const quieted = editor.store.get().said;
    edit(editor, "similar", null);
    fieldFocused(editor);
    expect(editor.store.get().said).toBe(quieted);
  });

  test("typing holds a draft apart from the history, until it's finished", () => {
    edit(editor, "own", null);
    expect(editor.store.get().draft).toMatchObject({ spanId: "own", page: 0, text: "was own" });
    typeInto(editor, "now");
    expect(editor.store.get().history.done).toEqual([]);
    finish(editor, true);
    expect(editor.store.get().draft).toBeNull();
    expect(editor.store.get().reading.spans.get("own")?.text).toBe("now");
    expect(editor.store.get().said.text).toBe("Changed to now");
  });

  test("a trouble is said as it appears or changes, not as its numbers tick by with each letter", () => {
    const glyphs = { a: 500, b: 500 }; // 5 pt each at 10 pt
    const doc = aDoc({ spans: [aSpan({ id: "ab", text: "ab" })], fonts: [aFont("Times-Roman", { glyphs })] });
    editor = createEditor(FILE, doc, 2);
    edit(editor, "ab", null);
    // 10 pt past a 10 pt line, then 15: the share ticks up, and what was said stands.
    const doubled = "about 100% too long for its place: shorten it, or it runs into what's beside it";
    typeInto(editor, "abab");
    expect(editor.store.get().said.text).toBe(doubled);
    typeInto(editor, "ababa");
    expect(editor.store.get().said.text).toBe(doubled);
    typeInto(editor, "ababac");
    expect(editor.store.get().said.text).toContain("no c in this font");
  });

  test("a letter no font we ship draws is said to be left out before Enter, as the render will say it after", () => {
    const glyphs = halfWideGlyphs("abcdefghijklmnopqrstuvwxyz ");
    // Room after it, so the space typed doesn't run it long: the letter is the one thing to say.
    const similar = aSpan({ id: "named", text: "abc", substitute: "Liberation Serif Bold", room_pt: 50 });
    const font = aFont("Times-Roman", { look_alike: "Liberation Serif Bold", glyphs });
    editor = createEditor(FILE, aDoc({ spans: [similar], fonts: [font] }), 2);
    edit(editor, "named", null);
    typeInto(editor, "abc ✓");
    expect(troublesIn(editor.store.get(), "abc ✓").said).toEqual([
      "“✓” can't be drawn in any font we have, and will be left out",
    ]);
  });

  test("Up or Down chooses how a long line fits, and the edit takes it while it's offered", () => {
    // Twenty letters at 5 pt each is 100 pt; one more runs 5 pt past, where every way out is offered.
    const glyphs = halfWideGlyphs("abcdefghijklmnopqrstuvwxyz");
    const long = aSpan({ id: "long", text: "abcdefghijklmnopqrst" });
    editor = createEditor(FILE, aDoc({ spans: [long], fonts: [aFont("Times-Roman", { glyphs })] }), 2);
    edit(editor, "long", null);
    typeInto(editor, `${long.text}u`);
    expect(editor.store.get().said.text).toBe(
      "about 5% too long for its place: shorten it, or it runs into what's beside it; Up or Down chooses how it fits",
    );

    // From leaving it long, the last, round to the first.
    expect(stepWayOut(editor, 1)).toBe(true);
    expect(editor.store.get().said.text).toBe(COPY.options.shrink?.label);
    expect(troublesIn(editor.store.get(), `${long.text}u`).said).toEqual([]);
    finish(editor, true);
    expect(editor.store.get().reading.edits).toEqual([
      { kind: "replace", span_id: "long", text: `${long.text}u`, strategy: "shrink" },
    ]);

    // Far longer, only leaving it long is offered: nothing to choose, and the edit is drawn as typed.
    edit(editor, "long", null);
    typeInto(editor, `${long.text}uvwxyz`);
    expect(stepWayOut(editor, 1)).toBe(false);
    finish(editor, true);
    expect(editor.store.get().reading.edits.at(-1)).toEqual({
      kind: "replace",
      span_id: "long",
      text: `${long.text}uvwxyz`,
    });
  });

  test("typing into text a form field draws says at once that an edit here is left out", () => {
    editor = createEditor(
      FILE,
      aDoc({ spans: [aSpan({ id: "field", form_field: true })], fonts: [aFont("Times-Roman")] }),
      2,
    );
    edit(editor, "field", null);
    expect(troublesIn(editor.store.get(), "was field").said).toEqual([COPY.form_field_not_edited]);
  });

  test("a pasted tab or line separator becomes a space, and a bidi control is dropped, since the server refuses new text with one", () => {
    // As pasted: a chat wraps a phone number in bidi controls; Pages breaks lines with U+2028.
    typed("own", "\u202A+1 555\t0100\u202C Invoices are\u2028due\u2029by\u0085May");
    expect(editor.store.get().reading.spans.get("own")?.text).toBe("+1 555 0100 Invoices are due by May");
  });

  test("emptied, the field says it can't be, and Enter keeps it open; Escape still leaves it as it was", () => {
    edit(editor, "own", null);
    typeInto(editor, "  ");
    expect(editor.store.get().said.text).toBe(COPY.empty);
    expect(troublesIn(editor.store.get(), "  ").said).toEqual([COPY.empty]);
    const said = editor.store.get().said;
    enter(editor);
    expect(editor.store.get().draft?.text).toBe("  ");
    // Said again, for a screen reader pressing Enter: the field stays.
    expect(editor.store.get().said.text).toBe(COPY.empty);
    expect(editor.store.get().said.count).not.toBe(said.count);
    typeInto(editor, "now");
    enter(editor);
    expect(editor.store.get().draft).toBeNull();
    expect(editor.store.get().reading.spans.get("own")?.text).toBe("now");
    expect(editor.store.get().focusTo).toEqual({ spanId: "own" });
  });

  test("Escape, the same words, or nothing at all put nothing in the history", () => {
    edit(editor, "own", null);
    typeInto(editor, "now");
    finish(editor, false);
    typed("own", "was own");
    typed("own", "   ");
    expect(editor.store.get().history.done).toEqual([]);
  });

  test("typing into text that won't match leaves why to the note as typing starts, not each letter's troubles", () => {
    const turned = aSpan({ id: "turned", fidelity: "approximate", why: { code: "turned_text", params: {} } });
    const glyphs = halfWideGlyphs("was turned");
    editor = createEditor(FILE, aDoc({ spans: [turned], fonts: [aFont("Times-Roman", { glyphs })] }), 2);
    edit(editor, "turned", null);
    expect(troublesIn(editor.store.get(), "was turned").said).toEqual([]);
  });

  test("a font quieted warns no more as typing starts, and is said; warned again, it's back", () => {
    quietFont(editor, "Named", { quiet: true });
    expect(editor.store.get().quietFonts).toEqual(new Set(["Named"]));
    expect(editor.store.get().said.text).toBe("Edits in Named won't warn again");
    quietFont(editor, "Named", { quiet: false });
    expect(editor.store.get().quietFonts).toEqual(new Set());
    expect(editor.store.get().said.text).toBe("Edits in Named warn again");
  });
});
