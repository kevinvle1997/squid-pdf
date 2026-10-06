import { beforeEach, describe, expect, test, vi } from "vitest";
import { userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import "../../styles/tokens.css";
import "../../styles/base.css";
import { ProblemError, putFont, render as renderOnServer } from "../../api/client";
import type { Render } from "../../api/types";
import { aDoc, aFont, aProblem, aReply, aSkipped, aSpan, aSpanNotice } from "../../fixtures";
import { EditorShell } from "./EditorShell";

vi.mock(import("../../api/client"), async (original) => ({
  ...(await original()),
  render: vi.fn(),
  stillThere: vi.fn(async () => true),
  putFont: vi.fn(),
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
