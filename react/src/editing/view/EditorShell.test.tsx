import { beforeEach, describe, expect, test, vi } from "vitest";
import { userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import "../../styles/tokens.css";
import "../../styles/base.css";
import { render as renderOnServer } from "../../api/client";
import type { Render } from "../../api/types";
import { aDoc, aFont, aNotice, aReply, aSkipped, aSpan } from "../../fixtures";
import { EditorShell } from "./EditorShell";

vi.mock(import("../../api/client"), async (original) => ({
  ...(await original()),
  render: vi.fn(),
  stillThere: vi.fn(async () => true),
}));

const span = aSpan({ id: "s1", text: "was here", size: 20, bbox: { x0: 72, y0: 100, x1: 152, y1: 124 } });
const DOC = aDoc({ spans: [span], fonts: [aFont("Times-Roman")] });

beforeEach(() => {
  vi.mocked(renderOnServer).mockReset();
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
      aReply({ notices: [aNotice(drewOtherwise, span.id)], skipped: [aSkipped(0, leftOut)] }),
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
