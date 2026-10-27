import { beforeEach, describe, expect, test, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import "@fontsource/funnel-sans/600.css";
import "../../styles/tokens.css";
import "../../styles/base.css";
import { pageUrl, renderStrips } from "../../api/client";
import type { Render } from "../../api/types";
import { A4, aDoc, aFont, aSpan, halfWideGlyphs, SENTENCES } from "../../fixtures";
import { createEditor, type Editor, undo } from "../editor";
import { fill } from "../words";
import { EditorContext } from "./context";
import { Page } from "./Page";

vi.mock(import("../../documents/localPersistence"), async (original) => ({
  ...(await original()),
  persistFile: vi.fn(async () => undefined),
  persistState: vi.fn(async () => undefined),
}));

vi.mock(import("../../api/client"), async (original) => ({
  ...(await original()),
  renderStrips: vi.fn(),
  pageUrl: vi.fn(),
  stillThere: vi.fn(async () => true),
  pageImage: vi.fn(async () => undefined),
}));

const WORDS = "Due 14 March 2026";
// A subset that drew only these words: it has no 7, so the 17th can't be written in it.
const due = aSpan({ id: "due", text: WORDS, size: 20, bbox: { x0: 72, y0: 100, x1: 242, y1: 124 }, room_pt: 200 });
const DOC = aDoc({ spans: [due], fonts: [aFont("Times-Roman", { glyphs: halfWideGlyphs(WORDS) })] });
const WHITE_PIXEL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR42mP4DwABAQEAHLCMmQAAAABJRU5ErkJggg==";
const NO_SEVEN = fill(SENTENCES.missing, { chars: ["7"], font: "Liberation Serif Regular" });

let editor: Editor;

beforeEach(async () => {
  vi.mocked(renderStrips)
    .mockReset()
    .mockReturnValue(new Promise<Render>(() => undefined));
  vi.mocked(pageUrl).mockReset().mockReturnValue(WHITE_PIXEL);
  editor = createEditor(new File(["%PDF-"], "invoice.pdf"), DOC, 2);
  await page.viewport(1400, 900);
});

/** The page, the field on its span reading `words` open from the keyboard, its words selected. */
async function openField(words = WORDS) {
  const screen = await render(
    <EditorContext.Provider value={editor}>
      <Page index={0} info={A4} shownNumber={1} />
    </EditorContext.Provider>,
  );
  const mark = screen.getByRole("button", { name: words });
  await expect.element(mark).toBeInTheDocument();
  mark.element().focus();
  await userEvent.keyboard("{Enter}");
  const field = screen.getByRole("textbox", { name: `Change “${words}”` });
  await expect.element(field).toHaveFocus();
  return { screen, field };
}

describe("a date in the field", () => {
  test("Alt+Down opens the calendar in the slip, beside the field, on the date's own day", async () => {
    const { screen } = await openField();
    const control = screen.getByRole("button", { name: "Date" });
    await expect.element(control).toHaveAttribute("aria-expanded", "false");
    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    await expect.element(control).toHaveAttribute("aria-expanded", "true");
    const fourteenth = screen.getByRole("button", { name: /March 14, 2026/ });
    await expect.element(fourteenth).toHaveFocus();
    await expect.element(screen.getByRole("heading", { name: "March 2026" })).toBeVisible();
    // Inside the slip, under the verdict: the field is still where it was, and still says the line looks identical.
    await expect.element(screen.getByText("looks identical")).toBeVisible();
  });

  test("a day the font can't write is marked, and says why before it's chosen", async () => {
    const { screen } = await openField();
    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    const seventeenth = screen.getByRole("button", { name: /March 17, 2026/ });
    await expect.element(seventeenth).toBeVisible();
    await userEvent.hover(seventeenth);
    await expect.element(screen.getByRole("tooltip")).toHaveTextContent(NO_SEVEN);
    // Heard on the keyboard as focus reaches it, in the server's words.
    await userEvent.keyboard("{ArrowRight}{ArrowRight}{ArrowRight}");
    expect(editor.store.get().announcement.text).toBe(NO_SEVEN);
  });

  test("choosing a day writes the field, the verdict follows, focus is back; Enter adds one replace, one undo takes it off", async () => {
    const { screen, field } = await openField();
    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    await screen.getByRole("button", { name: /March 17, 2026/ }).click();

    await expect.element(field).toHaveValue("Due 17 March 2026");
    await expect.element(field).toHaveFocus();
    await expect.element(screen.getByText(NO_SEVEN)).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "Date" })).toHaveAttribute("aria-expanded", "false");

    await userEvent.keyboard("{Enter}");
    const { done } = editor.store.get().history;
    expect(done.map((step) => step.map(({ edit }) => edit))).toEqual([
      [{ kind: "replace", span_id: "due", text: "Due 17 March 2026" }],
    ]);
    undo(editor);
    expect(editor.store.get().reading.spans.get("due")).toBeUndefined();
  });

  test("Escape closes the calendar and keeps the typing, focus back in the field", async () => {
    const { screen, field } = await openField();
    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    await expect.element(screen.getByRole("button", { name: /March 14, 2026/ })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    await expect.element(field).toHaveFocus();
    await expect.element(screen.getByRole("button", { name: "Date" })).toHaveAttribute("aria-expanded", "false");
    expect(editor.store.get().draft?.text).toBe(WORDS);
  });
});

describe("an amount in the field", () => {
  test("Enter in its number field writes it as the page writes amounts, and focus goes back to the field", async () => {
    const words = "Pay 1.250,00 €";
    const amount = aSpan({ id: "due", text: words, size: 20, bbox: due.bbox, room_pt: 200 });
    editor = createEditor(
      new File(["%PDF-"], "invoice.pdf"),
      aDoc({ spans: [amount], fonts: [aFont("Times-Roman", { glyphs: halfWideGlyphs(`${words}0123456789`) })] }),
      2,
    );
    const { screen, field } = await openField(words);
    await screen.getByRole("button", { name: "Amount" }).click();
    const number = screen.getByRole("textbox", { name: "Amount" });
    await expect.element(number).toHaveFocus();
    await number.fill("980.5");
    await userEvent.keyboard("{Enter}");
    await expect.element(field).toHaveValue("Pay 980,50 €");
    await expect.element(field).toHaveFocus();
  });
});
