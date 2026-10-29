import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import { learnDateLanguages } from "../dateNames";
import "@fontsource/funnel-sans/600.css";
import "../../styles/tokens.css";
import "../../styles/base.css";
import { pageUrl, renderStrips } from "../../api/client";
import type { Render } from "../../api/types";
import { A4, aDoc, aFont, aSpan, halfWideGlyphs, SENTENCES } from "../../fixtures";
import { animationsDone, emulateTouchScreen, middleOf, tapAt } from "../../testing";
import { createEditor, type Editor, undo } from "../editor";
import { typeInto } from "../typing";
import { fill } from "../words";
import { EditorContext } from "./context";
import { ALT } from "./keys";
import { Page } from "./Page";

// Learning as an editor does, unless a test holds it back.
vi.mock(import("../dateNames"), async (original) => {
  const learnt = await original();
  return { ...learnt, learnDateLanguages: vi.fn(learnt.learnDateLanguages) };
});

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

// Every language a date is read in, learnt in the background as an editor would: the dates here need them.
beforeAll(() => learnDateLanguages());

const WORDS = "Due 14 March 2026";
// A subset that drew only these words: it has no 7, so the 17th can't be written in it.
const due = aSpan({ id: "due", text: WORDS, size: 20, bbox: { x0: 72, y0: 100, x1: 242, y1: 124 }, room_pt: 200 });
const DOC = aDoc({ spans: [due], fonts: [aFont("Times-Roman", { glyphs: halfWideGlyphs(WORDS) })] });
const WHITE_PIXEL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR42mP4DwABAQEAHLCMmQAAAABJRU5ErkJggg==";
const NO_SEVEN = fill(SENTENCES.missing, { chars: ["7"], font: "Liberation Serif Regular" });
// What a day the font can't write says, as the mockup draws it: the full verdict is the slip's.
const DAY_NO_SEVEN = fill(SENTENCES.not_in_font, { chars: ["7"] });

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

/** The marked day's reason shown, on its row under the month: none while it shows none. */
function reasonIn(screen: Awaited<ReturnType<typeof render>>): HTMLElement | null {
  const under = screen.getByRole("grid").element().nextElementSibling;
  return under?.querySelector<HTMLElement>("[data-shown]") ?? null;
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

  test("Tab on out of the open calendar, with nothing after it to go to, keeps the edit and puts focus on the span", async () => {
    const { screen } = await openField();
    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    await expect.element(screen.getByRole("button", { name: /March 14, 2026/ })).toHaveFocus();
    await userEvent.keyboard("{Tab}");
    await expect.element(screen.getByRole("button", { name: WORDS })).toHaveFocus();
  });

  test("a day the font can't write is marked, and says why in a few words before it's chosen", async () => {
    const { screen } = await openField();
    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    const seventeenth = screen.getByRole("button", { name: /March 17, 2026/ });
    await expect.element(seventeenth).toBeVisible();
    await userEvent.hover(seventeenth);
    await expect.poll(() => reasonIn(screen)?.textContent).toBe(DAY_NO_SEVEN);
    // Heard on the keyboard as focus reaches it, in the server's words.
    await userEvent.keyboard("{ArrowRight}{ArrowRight}{ArrowRight}");
    expect(editor.store.get().announcement.text).toBe(DAY_NO_SEVEN);
  });

  test.each([1440, 390])(
    "on a touch screen %i wide, a marked day's reason shows on its own row under the month, over no button",
    async (width) => {
      await page.viewport(width, 900);
      await emulateTouchScreen();
      const { screen, field } = await openField();
      // A letter the font lacks typed already: the slip offers the user's copy, and Redact sits along from it.
      await field.fill("Due 17 March 2026");
      const control = screen.getByRole("button", { name: "Date" });
      await expect.element(control).toBeVisible();
      // Tapped where it comes to rest: the slip arrives moving.
      await animationsDone();
      await tapAt(middleOf(control.element().getBoundingClientRect()));
      const marked = screen.getByRole("button", { name: /March 27, 2026/ });
      await expect.element(marked).toBeVisible();
      const slip = control.element().closest("[data-slip]");
      const height = slip?.getBoundingClientRect().height;
      await tapAt(middleOf(marked.element().getBoundingClientRect()));
      await expect.poll(() => reasonIn(screen)).not.toBeNull();
      // Its line was kept for it: the slip doesn't grow as it shows.
      expect(slip?.getBoundingClientRect().height).toBe(height);
      const reason = reasonIn(screen)?.getBoundingClientRect() ?? new DOMRect();
      for (const button of document.querySelectorAll('button, [role="button"]')) {
        const box = button.getBoundingClientRect();
        const covers =
          box.left < reason.right && reason.left < box.right && box.top < reason.bottom && reason.top < box.bottom;
        expect(covers, `covers ${button.textContent}`).toBe(false);
      }
    },
  );

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

  test("typed away while its calendar has focus, gives focus back to the field, never to the page's body", async () => {
    const { screen, field } = await openField();
    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    await expect.element(screen.getByRole("button", { name: /March 14, 2026/ })).toHaveFocus();
    // As the browser's own undo of the field would type it, out of sight of it.
    typeInto(editor, "Due ");
    await expect.element(screen.getByRole("grid")).not.toBeInTheDocument();
    await expect.element(field).toHaveFocus();
    expect(editor.store.get().focusTo).toBeNull();
  });

  test("open, Alt+Down from the field goes back into it, on the month stepped to; Date still closes it", async () => {
    const { screen, field } = await openField();
    const control = screen.getByRole("button", { name: "Date" });
    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    await expect.element(screen.getByRole("button", { name: /March 14, 2026/ })).toHaveFocus();
    // The step drawn in the month's header: React Aria keeps a second for screen readers, after the days.
    await screen.getByRole("button", { name: "Next" }).first().click();
    await expect.element(screen.getByRole("heading", { name: "April 2026" })).toBeVisible();
    // Back in the field, the calendar left open beside it, as a click leaves it.
    await field.click();
    await expect.element(field).toHaveFocus();
    await userEvent.keyboard("{Home}x");
    await expect.element(field).toHaveValue(`x${WORDS}`);

    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    await expect.element(screen.getByRole("button", { name: /April 14, 2026/ })).toHaveFocus();
    await expect.element(screen.getByRole("heading", { name: "April 2026" })).toBeVisible();
    await control.click();
    await expect.element(control).toHaveAttribute("aria-expanded", "false");
  });

  test("keeps the slip's height as the months are stepped through, into another year and back", async () => {
    // The 15th and 17th can't be written, nor any month named with a letter "Due 14 March 2026" hasn't, nor 2027.
    const { screen } = await openField();
    const control = screen.getByRole("button", { name: "Date" });
    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    await expect.element(screen.getByRole("button", { name: /March 14, 2026/ })).toHaveFocus();
    const slip = control.element().closest("[data-slip]");
    // Measured at rest: the slip arrives moving.
    await animationsDone();
    const heightNow = () => slip?.getBoundingClientRect().height;
    const height = heightNow();

    const heights: (number | undefined)[] = [];
    for (const key of [...Array(12).fill("{PageDown}"), ...Array(12).fill("{PageUp}")]) {
      await userEvent.keyboard(key);
      await new Promise(requestAnimationFrame);
      heights.push(heightNow());
    }
    expect(heights).toEqual(heights.map(() => height));
  });
});

describe("the control's key", () => {
  test("shows on Date, as a picker's does, and not on Amount, though Alt+Down opens both", async () => {
    const words = "Pay 1.250,00 €";
    const amount = aSpan({ id: "pay", text: words, size: 20, bbox: { x0: 72, y0: 200, x1: 242, y1: 224 } });
    const glyphs = halfWideGlyphs(`${WORDS}${words}0123456789`);
    editor = createEditor(
      new File(["%PDF-"], "invoice.pdf"),
      aDoc({ spans: [due, amount], fonts: [aFont("Times-Roman", { glyphs })] }),
      2,
    );
    const screen = await render(
      <EditorContext.Provider value={editor}>
        <Page index={0} info={A4} shownNumber={1} />
      </EditorContext.Provider>,
    );
    // Opened by a press, as a mouse does: after a key, React Aria shows no tooltip on hover.
    await screen.getByRole("button", { name: words }).click();
    await screen.getByRole("button", { name: "Amount" }).hover();
    await new Promise((shown) => setTimeout(shown, 900));
    expect(screen.getByRole("tooltip").query()).toBeNull();
    await userEvent.keyboard("{Escape}");
    await screen.getByRole("button", { name: WORDS }).click();
    // The pointer moves onto Date, which is drawn where Amount was.
    await screen.getByRole("button", { name: "Redact" }).hover();
    await screen.getByRole("button", { name: "Date" }).hover();
    await expect.element(screen.getByText(`${ALT}↓`)).toBeVisible();
  });
});

describe("an amount in the field", () => {
  // A date and an amount on one line, each letter 6 points wide.
  const BOTH = "Due 14 March 2026, total 1.250,00 €";

  // What a paste puts before the line: longer than the amount, so the amount moves past where it lay.
  const PASTED = "INVOICE NUMBER ABCDEFGHIJKLMNOP: ";

  /** The editor on a line of `words`, its date languages held back until what's returned is called. */
  function holdBackDates(words = BOTH): () => void {
    let learnt: () => void = () => undefined;
    vi.mocked(learnDateLanguages).mockReturnValueOnce(new Promise<void>((done) => (learnt = done)));
    const both = aSpan({ id: "due", text: words, size: 12, bbox: due.bbox, room_pt: 400 });
    const glyphs = halfWideGlyphs(`${words}${PASTED}0123456789`);
    editor = createEditor(
      new File(["%PDF-"], "invoice.pdf"),
      aDoc({ spans: [both], fonts: [aFont("Times-Roman", { glyphs })] }),
      2,
    );
    return () => learnt();
  }

  /** The field on `BOTH` opened by a click on its date, as a mouse does. */
  async function clickDate() {
    const screen = await render(
      <EditorContext.Provider value={editor}>
        <Page index={0} info={A4} shownNumber={1} />
      </EditorContext.Provider>,
    );
    const mark = screen.getByRole("button", { name: BOTH });
    await expect.element(mark).toBeInTheDocument();
    const { width, height } = mark.element().getBoundingClientRect();
    const marchPt = (BOTH.indexOf("March") + 2) * 6;
    await mark.click({ position: { x: (width * marchPt) / (due.bbox.x1 - due.bbox.x0), y: height / 2 } });
    const field = screen.getByRole("textbox", { name: `Change “${BOTH}”` });
    await expect.element(field).toHaveFocus();
    return { screen, field };
  }

  test("offered before the dates are read, gives way to the date pressed on once they are, nothing done with it", async () => {
    const learnt = holdBackDates();
    const { screen } = await clickDate();
    await expect.element(screen.getByRole("button", { name: "Amount" })).toBeVisible();

    learnt();
    await expect.element(screen.getByRole("button", { name: "Date" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Amount" }).query()).toBeNull();
  });

  test("offered before the dates are read, stays once they are, typed in", async () => {
    const learnt = holdBackDates();
    const { screen, field } = await clickDate();
    await expect.element(screen.getByRole("button", { name: "Amount" })).toBeVisible();
    await userEvent.keyboard("{End} ");
    await expect.element(field).toHaveValue(`${BOTH} `);

    learnt();
    await expect.poll(() => editor.store.get().dates).not.toBeNull();
    await expect.element(screen.getByRole("button", { name: "Amount" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Date" }).query()).toBeNull();
  });

  test("held, typed in, stays as a paste before it moves it further than its own length", async () => {
    const learnt = holdBackDates();
    const { screen, field } = await openField(BOTH);
    await expect.element(screen.getByRole("button", { name: "Amount" })).toBeVisible();
    await userEvent.keyboard("{End} ");
    learnt();
    await expect.poll(() => editor.store.get().dates).not.toBeNull();

    await field.fill(`${PASTED}${BOTH} `);
    await expect.element(screen.getByRole("button", { name: "Amount" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Date" }).query()).toBeNull();
  });

  /** The field on `words`, its Amount editor opened, the dates read, and focus pressed back into the field. */
  async function backInTheField(words = BOTH) {
    const learnt = holdBackDates(words);
    const { screen, field } = await openField(words);
    await screen.getByRole("button", { name: "Amount" }).click();
    await expect.element(screen.getByRole("textbox", { name: "Amount" })).toHaveFocus();
    learnt();
    await expect.poll(() => editor.store.get().dates).not.toBeNull();
    await field.click();
    await expect.element(field).toHaveFocus();
    return { screen, field };
  }

  test("open, stays open on its amount as a paste before it moves it, focus staying in the field", async () => {
    const { screen, field } = await backInTheField();

    await field.fill(`${PASTED}${BOTH}`);
    await expect.element(screen.getByRole("textbox", { name: "Amount" })).toBeVisible();
    expect(screen.getByRole("grid").query()).toBeNull();
    await expect.element(field).toHaveFocus();
  });

  test("open, stays open on its amount as words before it are deleted, focus staying in the field", async () => {
    const words = "Due 14 March 2026, total amount payable now 1.250,00 €";
    const { screen, field } = await backInTheField(words);
    const input = field.element();
    if (!(input instanceof HTMLInputElement)) throw new Error("no field");
    const gone = "amount payable now ";
    input.setSelectionRange(words.indexOf(gone), words.indexOf(gone) + gone.length);

    await userEvent.keyboard("{Backspace}");
    await expect.element(field).toHaveValue(BOTH);
    await expect.element(screen.getByRole("textbox", { name: "Amount" })).toBeVisible();
    expect(screen.getByRole("grid").query()).toBeNull();
    await expect.element(field).toHaveFocus();
  });

  test("open, closes once its amount is typed away, focus staying in the field", async () => {
    const { screen, field } = await backInTheField();

    await field.fill("Due 14 March 2026, total");
    await expect.element(screen.getByRole("button", { name: "Date" })).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "Date" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("grid").query()).toBeNull();
    await expect.element(field).toHaveFocus();
    // Taken where it was: none left waiting to pull focus back once the field is left.
    expect(editor.store.get().focusTo).toBeNull();
  });

  test("open, a press into its number field takes focus there, as the slip's other presses don't", async () => {
    const { screen } = await backInTheField();
    const number = screen.getByRole("textbox", { name: "Amount" });
    await number.click();
    await expect.element(number).toHaveFocus();
  });

  test("open, Alt+Down from the field goes back into its number field", async () => {
    const { screen, field } = await backInTheField();
    await userEvent.keyboard("{Home}x");
    await expect.element(field).toHaveValue(`x${BOTH}`);

    await userEvent.keyboard("{Alt>}{ArrowDown}{/Alt}");
    await expect.element(screen.getByRole("textbox", { name: "Amount" })).toHaveFocus();
  });

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

  test("has its control as the edit starts, before the languages behind dates are learnt", async () => {
    vi.mocked(learnDateLanguages).mockReturnValueOnce(new Promise<void>(() => undefined));
    const words = "Pay 1.250,00 €";
    const amount = aSpan({ id: "due", text: words, size: 20, bbox: due.bbox, room_pt: 200 });
    editor = createEditor(new File(["%PDF-"], "invoice.pdf"), aDoc({ spans: [amount], fonts: DOC.fonts }), 2);
    const { screen } = await openField(words);
    await expect.element(screen.getByRole("button", { name: "Amount" })).toBeVisible();
  });

  test("stays open, with what's typed in it, as the dates finish being read beside it", async () => {
    const learnt = holdBackDates();
    const { screen } = await openField(BOTH);
    const control = screen.getByRole("button", { name: "Amount" });
    await control.click();
    const number = screen.getByRole("textbox", { name: "Amount" });
    await expect.element(number).toHaveFocus();
    await userEvent.keyboard("{Control>}a{/Control}980");
    const slip = control.element().closest("[data-slip]")?.getBoundingClientRect().height;

    learnt();
    await expect.poll(() => editor.store.get().dates).not.toBeNull();
    // The dates are read, and the date is the first value: the Amount the user chose stays all the same.
    expect(editor.store.get().dates).not.toBeNull();
    await expect.element(number).toHaveFocus();
    await expect.element(number).toHaveValue(expect.stringContaining("980"));
    expect(screen.getByRole("button", { name: "Date" }).query()).toBeNull();
    expect(control.element().closest("[data-slip]")?.getBoundingClientRect().height).toBe(slip);
  });
});
