import { beforeEach, expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";
import "./styles/tokens.css";
import "./styles/base.css";
import { userEvent } from "vitest/browser";
import { App } from "./App";
import { upload } from "./api/client";
import type { Render } from "./api/types";
import { forgetKept, keepFile, keepState, keptDocument } from "./documents/kept";
import { EMPTY_HISTORY, historyReducer } from "./editing/history";
import { aDoc, aFont, aSpan } from "./fixtures";

vi.mock(import("./api/client"), async (original) => ({
  ...(await original()),
  upload: vi.fn(),
  render: vi.fn(() => new Promise<Render>(() => undefined)),
  stillThere: vi.fn(async () => true),
  pageImage: vi.fn(async () => undefined),
}));

beforeEach(async () => {
  vi.mocked(upload).mockReset();
  // The browser's real storage, one test at a time: each starts with nothing kept.
  await forgetKept();
});

// The server keeps it an hour from now, as it says on every read.
const KEPT_DOC = aDoc({
  id: "kept",
  spans: [aSpan({ id: "a" })],
  fonts: [aFont("Times-Roman")],
  expires_at: new Date(Date.now() + 3_600_000).toISOString(),
});

/** Keep `contract.pdf` here as an editor would, its one change "is a", until `until`. */
async function keptHere(until: number) {
  const history = historyReducer(EMPTY_HISTORY, {
    kind: "add",
    edits: [{ kind: "replace", span_id: "a", text: "is a" }],
  });
  await keepFile(new File(["%PDF-"], "contract.pdf", { type: "application/pdf" }));
  await keepState({ doc: KEPT_DOC, work: { history, fonts: [] }, until });
}

/** A PDF chosen on the landing, as its file picker hands one over. */
function choose(container: HTMLElement, name: string) {
  const input = container.querySelector("input[type=file]") as HTMLInputElement;
  const files = new DataTransfer();
  files.items.add(new File(["%PDF-"], name, { type: "application/pdf" }));
  input.files = files.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

test("a bug while opening is said, and another file can be chosen", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.mocked(upload).mockRejectedValue(new TypeError("request.response is null"));
  const screen = await render(<App />);
  choose(screen.container, "contract.pdf");
  await expect.element(screen.getByText("Something went wrong", { exact: false })).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Choose a PDF" })).toBeEnabled();
});

test("a PDF dropped on the editor opens in place of the one open, as the landing opens one", async () => {
  const fonts = [aFont("Times-Roman")];
  vi.mocked(upload)
    .mockResolvedValueOnce(aDoc({ id: "first", spans: [aSpan({ id: "a" })], fonts }))
    .mockResolvedValueOnce(aDoc({ id: "second", spans: [aSpan({ id: "b" })], fonts }));
  const screen = await render(<App />);
  choose(screen.container, "contract.pdf");
  await expect.element(screen.getByText("contract.pdf")).toBeVisible();

  const files = new DataTransfer();
  files.items.add(new File(["%PDF-"], "invoice.pdf", { type: "application/pdf" }));
  const drop = new DragEvent("drop", { dataTransfer: files, bubbles: true, cancelable: true });
  document.body.dispatchEvent(drop);
  // Never the browser's: it would open the file in this tab.
  expect(drop.defaultPrevented).toBe(true);
  await expect.element(screen.getByText("invoice.pdf")).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "was b" })).toBeInTheDocument();
});

test("a change is kept in this browser as it's made, so the landing offers it after a reload", async () => {
  vi.mocked(upload).mockResolvedValueOnce(KEPT_DOC);
  const first = await render(<App />);
  choose(first.container, "contract.pdf");
  const mark = first.getByRole("button", { name: "was a" });
  await expect.element(mark).toBeInTheDocument();
  mark.element().focus();
  await userEvent.keyboard("{Enter}");
  await first.getByRole("textbox").fill("is a");
  await userEvent.keyboard("{Enter}");
  await expect.poll(async () => (await keptDocument())?.file.name).toBe("contract.pdf");

  await first.unmount();
  const again = await render(<App />);
  await expect
    .element(again.getByText("Your changes to contract.pdf are kept in this browser until", { exact: false }))
    .toBeVisible();
});

test("a document kept here opens again with its changes, without sending the file again", async () => {
  await keptHere(Date.now() + 60_000);
  const screen = await render(<App />);
  await screen.getByRole("button", { name: "Open it again" }).click();
  await expect.element(screen.getByRole("button", { name: "is a" })).toBeInTheDocument();
  expect(upload).not.toHaveBeenCalled();
});

test("Delete it now forgets the document kept here, file and all", async () => {
  await keptHere(Date.now() + 60_000);
  const screen = await render(<App />);
  await screen.getByRole("button", { name: "Delete it now" }).click();
  await expect.element(screen.getByRole("button", { name: "Open it again" })).not.toBeInTheDocument();
  await expect.poll(() => keptDocument()).toBeNull();
});

test("one past the server's hour isn't offered, and is forgotten", async () => {
  await keptHere(Date.now() - 1);
  const screen = await render(<App />);
  await expect.element(screen.getByRole("button", { name: "Choose a PDF" })).toBeVisible();
  await expect.poll(() => keptDocument()).toBeNull();
  await expect.element(screen.getByRole("button", { name: "Open it again" })).not.toBeInTheDocument();
});
