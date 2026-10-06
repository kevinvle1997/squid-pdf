import { beforeEach, describe, expect, test, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import "../../styles/tokens.css";
import "../../styles/base.css";
import { exportPdf, ProblemError, putFont, render as renderOnServer, stillThere } from "../../api/client";
import type { Render } from "../../api/types";
import { A4, aDoc, aFont, aProblem, aReply, aSkipped, aSpan, aSpanNotice } from "../../fixtures";
import { EditorShell } from "./EditorShell";

vi.mock(import("../../api/client"), async (original) => ({
  ...(await original()),
  render: vi.fn(),
  stillThere: vi.fn(async () => true),
  putFont: vi.fn(),
  exportPdf: vi.fn(),
}));

const span = aSpan({ id: "s1", text: "was here", size: 20, bbox: { x0: 72, y0: 100, x1: 152, y1: 124 } });
const DOC = aDoc({ spans: [span], fonts: [aFont("Times-Roman")] });

beforeEach(() => {
  vi.mocked(renderOnServer).mockReset();
  vi.mocked(putFont).mockReset();
});

async function change(screen: Awaited<ReturnType<typeof render>>, text: string) {
  const mark = screen.getByRole("button", { name: "was here" });
  await expect.element(mark).toBeInTheDocument();
  mark.element().focus();
  await userEvent.keyboard("{Enter}");
  await screen.getByRole("textbox").fill(text);
  await userEvent.keyboard("{Enter}");
}

describe("the editor", () => {
  test("what the server's render says it did other than asked is said under the bar", async () => {
    const drewOtherwise = "This page wouldn't take Times, so the line is drawn in Liberation Serif.";
    const leftOut = "An edit points at text that isn't in this document, so it was left out.";
    vi.mocked(renderOnServer).mockResolvedValue(
      aReply({ notices: [aSpanNotice(drewOtherwise, span.id)], skipped: [aSkipped(0, leftOut)] }),
    );
    const screen = await render(<EditorShell file={new File(["%PDF-"], "contract.pdf")} opened={DOC} />);
    await change(screen, "is here");
    await expect.element(screen.getByText(drewOtherwise)).toBeVisible();
    await expect.element(screen.getByText(leftOut)).toBeVisible();
  });

  test("every notice the document opened with is shown, not only the first", async () => {
    vi.mocked(renderOnServer).mockReturnValue(new Promise<Render>(() => undefined));
    const notices = [
      { code: "a", params: {}, type: "t", detail: "Some text is in a font we can't use." },
      { code: "b", params: {}, type: "t", detail: "This file is signed; editing breaks the signature." },
    ];
    const screen = await render(
      <EditorShell file={new File(["%PDF-"], "contract.pdf")} opened={{ ...DOC, notices }} />,
    );
    await expect.element(screen.getByText(notices[0]?.detail ?? "")).toBeVisible();
    await expect.element(screen.getByText(notices[1]?.detail ?? "")).toBeVisible();
  });
});

describe("the bar", () => {
  test("says when the document couldn't be opened again, where the document's name is", async () => {
    vi.mocked(renderOnServer).mockReturnValue(new Promise<Render>(() => undefined));
    const unreachable = "We can't work on files right now. Try again in a few minutes.";
    vi.mocked(stillThere).mockRejectedValueOnce(new ProblemError(aProblem(503, unreachable)));
    const screen = await render(<EditorShell file={new File(["%PDF-"], "contract.pdf")} opened={DOC} />);
    // The page image fails to load here, as it does once the server has lost the document.
    await expect.element(screen.getByRole("banner").getByText(unreachable)).toBeVisible();
    expect(screen.getByText(unreachable).elements()).toHaveLength(1);
    // The sentence wraps; the bar's buttons keep their one line.
    const [wide, tall] = [window.innerWidth, window.innerHeight];
    await page.viewport(900, 700);
    const exportButton = screen.getByRole("button", { name: /Export/ }).element();
    expect(exportButton.getBoundingClientRect().height).toBeLessThan(40);
    await page.viewport(wide, tall);
  });
});

describe("export", () => {
  test("says it's under way only past the quiet spell, so a quick one shows nothing", async () => {
    vi.mocked(renderOnServer).mockReturnValue(new Promise<Render>(() => undefined));
    vi.mocked(exportPdf).mockReturnValue(new Promise(() => undefined));
    const screen = await render(<EditorShell file={new File(["%PDF-"], "contract.pdf")} opened={DOC} />);
    await screen.getByRole("button", { name: /Export/ }).click();
    const exporting = screen.getByRole("banner").getByText("Exporting");
    await expect.element(exporting).toBeInTheDocument();
    await expect.element(exporting).not.toBeVisible();
    await expect.element(exporting).toBeVisible();
  });
});

describe("leaving the page", () => {
  test("asks first only while there are edits since the last export", async () => {
    vi.mocked(renderOnServer).mockReturnValue(new Promise<Render>(() => undefined));
    const screen = await render(<EditorShell file={new File(["%PDF-"], "contract.pdf")} opened={DOC} />);
    const leave = () => {
      const leaving = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(leaving);
      return leaving.defaultPrevented;
    };
    await expect.element(screen.getByRole("button", { name: "was here" })).toBeInTheDocument();
    expect(leave()).toBe(false);
    await change(screen, "is here");
    expect(leave()).toBe(true);
  });
});

describe("undo and redo", () => {
  test("are heard, the same words again too, and bring their span into view while focus stays", async () => {
    vi.mocked(renderOnServer).mockReturnValue(new Promise<Render>(() => undefined));
    // The span is on the third page, well below the first screen.
    const far = aSpan({ ...span, page: 2 });
    const doc = aDoc({ spans: [far], fonts: [aFont("Times-Roman")], pages: [A4, A4, A4] });
    const screen = await render(<EditorShell file={new File(["%PDF-"], "contract.pdf")} opened={doc} />);
    screen.getByRole("region", { name: "Page 3" }).element().scrollIntoView();
    await change(screen, "is here");
    await userEvent.keyboard("{Control>}z{/Control}");
    const status = screen.getByRole("status");
    await expect.element(status).toHaveTextContent("Back to was here");
    const firstSaid = status.element().firstElementChild;

    await userEvent.keyboard("{Control>}{Shift>}z{/Shift}{/Control}");
    await expect.element(status).toHaveTextContent("Changed to is here");
    const focused = document.activeElement;
    window.scrollTo(0, 0);
    await userEvent.keyboard("{Control>}z{/Control}");

    await expect.element(status).toHaveTextContent("Back to was here");
    expect(status.element().firstElementChild).not.toBe(firstSaid);
    await vi.waitFor(() => expect(window.scrollY).toBeGreaterThan(0));
    expect(document.activeElement).toBe(focused);
  });
});

// Arial only named: a similar font stands in, and its letters are the ones the page lists.
const LETTERS = Object.fromEntries([..."abcdefghijklmnopqrstuvwxyz "].map((letter) => [letter, 500]));
const NAMED = aFont("Arial", { substitute: "Liberation Sans Regular", glyphs: LETTERS });
const ARIAL_SPAN = aSpan({ ...span, font: "Arial" });
const ARIAL_DOC = aDoc({ spans: [ARIAL_SPAN], fonts: [NAMED] });
const USERS_COPY = aDoc({
  spans: [ARIAL_SPAN],
  fonts: [aFont("Arial", { attached: true, glyphs: { ...LETTERS, Y: 667 } })],
});
const FONT_FILE = new File(["a font"], "arial.ttf", { type: "font/ttf" });
const MISMATCH = "This isn't the font the document uses. Its letters are a different width.";

async function typeInto(screen: Awaited<ReturnType<typeof render>>, text: string) {
  const mark = screen.getByRole("button", { name: "was here" });
  await expect.element(mark).toBeInTheDocument();
  mark.element().focus();
  await userEvent.keyboard("{Enter}");
  await screen.getByRole("textbox").fill(text);
}

async function attachThroughFonts(screen: Awaited<ReturnType<typeof render>>) {
  await screen.getByRole("button", { name: "Fonts" }).click();
  await expect.element(screen.getByRole("button", { name: "Use your copy of Arial" })).toBeVisible();
  // FileTrigger's input sits beside its button, unlabelled: the row's one file input.
  const chooser = screen.getByRole("dialog").element().querySelector("input[type=file]");
  if (!(chooser instanceof HTMLInputElement)) throw new Error("no file input in the fonts list");
  await userEvent.upload(chooser, FONT_FILE);
}

describe("the user's own copy of a font", () => {
  test("is offered while typing only once a letter typed is one the font can't draw", async () => {
    vi.mocked(renderOnServer).mockReturnValue(new Promise<Render>(() => undefined));
    const screen = await render(<EditorShell file={new File(["%PDF-"], "letter.pdf")} opened={ARIAL_DOC} />);

    await typeInto(screen, "was there");
    await expect.element(screen.getByRole("button", { name: "Use your copy" })).not.toBeInTheDocument();
    await screen.getByRole("textbox").fill("was Yes");
    await expect.element(screen.getByRole("button", { name: "Use your copy" })).toBeVisible();
  });

  test("a font file dropped on the field is the user's copy of the span's font", async () => {
    vi.mocked(renderOnServer).mockReturnValue(new Promise<Render>(() => undefined));
    vi.mocked(putFont).mockResolvedValue(USERS_COPY);
    const screen = await render(<EditorShell file={new File(["%PDF-"], "letter.pdf")} opened={ARIAL_DOC} />);
    await typeInto(screen, "was Yes");

    const field = screen.getByRole("textbox").element();
    // A file dragged from the desktop has an entry in the file system; one built here has none.
    const entry = vi.spyOn(DataTransferItem.prototype, "webkitGetAsEntry");
    entry.mockReturnValue({ isFile: true } as FileSystemEntry);
    const dropped = new DataTransfer();
    dropped.items.add(FONT_FILE);
    dropped.effectAllowed = "copy"; // as a file dragged from the desktop allows
    for (const type of ["dragenter", "dragover", "drop"]) {
      field.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dropped }));
    }

    await vi.waitFor(() => expect(putFont).toHaveBeenCalledWith(ARIAL_DOC.id, "Arial", FONT_FILE));
    entry.mockRestore();
  });

  test("text dragged onto the field is the field's to take, not a font", async () => {
    vi.mocked(renderOnServer).mockReturnValue(new Promise<Render>(() => undefined));
    const screen = await render(<EditorShell file={new File(["%PDF-"], "letter.pdf")} opened={ARIAL_DOC} />);
    await typeInto(screen, "was Yes");

    const field = screen.getByRole("textbox").element();
    const dragged = new DataTransfer();
    dragged.setData("text/plain", "there");
    dragged.effectAllowed = "copyMove"; // as text dragged from a page allows
    const events = ["dragenter", "dragover", "drop"].map(
      (type) => new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dragged }),
    );
    for (const event of events) field.dispatchEvent(event);

    expect(events.map((event) => event.defaultPrevented)).toEqual([false, false, false]);
    expect(putFont).not.toHaveBeenCalled();
  });

  test("a file picker taking the window's focus leaves the typing as it was", async () => {
    vi.mocked(renderOnServer).mockReturnValue(new Promise<Render>(() => undefined));
    const screen = await render(<EditorShell file={new File(["%PDF-"], "letter.pdf")} opened={ARIAL_DOC} />);
    await typeInto(screen, "was Yes");
    const windowLeft = vi.spyOn(document, "hasFocus").mockReturnValue(false);

    screen
      .getByRole("textbox")
      .element()
      .dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    windowLeft.mockRestore();

    await expect.element(screen.getByRole("textbox")).toHaveValue("was Yes");
  });

  test("attached, its font draws the span and nothing pops up to say so", async () => {
    vi.mocked(renderOnServer).mockReturnValue(new Promise<Render>(() => undefined));
    vi.mocked(putFont).mockResolvedValue(USERS_COPY);
    const screen = await render(<EditorShell file={new File(["%PDF-"], "letter.pdf")} opened={ARIAL_DOC} />);

    await attachThroughFonts(screen);

    expect(putFont).toHaveBeenCalledWith(ARIAL_DOC.id, "Arial", FONT_FILE);
    await expect.element(screen.getByRole("button", { name: "Remove your copy of Arial" })).toBeVisible();
    // The button pressed is gone: focus goes to the one in its place, never to the page's body.
    await expect.element(screen.getByRole("button", { name: "Remove your copy of Arial" })).toHaveFocus();
    await expect.element(screen.getByRole("alert")).not.toBeInTheDocument();
  });

  test("refused, the server's sentence shows beside the font, warned", async () => {
    vi.mocked(renderOnServer).mockReturnValue(new Promise<Render>(() => undefined));
    vi.mocked(putFont).mockRejectedValue(new ProblemError(aProblem(422, MISMATCH)));
    const screen = await render(<EditorShell file={new File(["%PDF-"], "letter.pdf")} opened={ARIAL_DOC} />);

    await attachThroughFonts(screen);

    await expect.element(screen.getByRole("dialog").getByText(MISMATCH)).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "Remove your copy of Arial" })).not.toBeInTheDocument();
  });
});
