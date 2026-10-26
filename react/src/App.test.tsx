import { beforeEach, expect, onTestFinished, test, vi } from "vitest";
import { render } from "vitest-browser-react";
import "./styles/tokens.css";
import "./styles/base.css";
import { userEvent } from "vitest/browser";
import { App } from "./App";
import { upload } from "./api/client";
import type { Render } from "./api/types";
import { SOMETHING_WENT_WRONG } from "./bugs";
import { forgetKept, forgetOrSayWhy, keepFile, keepState, keptDocument } from "./documents/kept";
import { EMPTY_HISTORY, historyReducer } from "./editing/history";
import { aDoc, aFont, aSpan } from "./fixtures";

vi.mock(import("./api/client"), async (original) => ({
  ...(await original()),
  upload: vi.fn(),
  renderStrips: vi.fn(() => new Promise<Render>(() => undefined)),
  stillThere: vi.fn(async () => true),
  pageImage: vi.fn(async () => undefined),
}));
// The browser's real storage, but a delete can be held under way.
vi.mock(import("./documents/kept"), async (original) => {
  const module = await original();
  return { ...module, forgetOrSayWhy: vi.fn(module.forgetOrSayWhy) };
});

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
  // The landing's words for a bug, not the crash screen's, which would also start "Something went wrong".
  await expect.element(screen.getByText(SOMETHING_WENT_WRONG)).toBeVisible();
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
  // The editor listens from an effect, which can run after its file's name shows: a drag over
  // the page is taken once it does, since both are listened for together.
  await expect
    .poll(() => {
      const over = new DragEvent("dragover", { dataTransfer: files, bubbles: true, cancelable: true });
      document.body.dispatchEvent(over);
      return over.defaultPrevented;
    })
    .toBe(true);
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

/** The browser's storage throwing `error` as anything kept here is deleted, until the test ends. */
function deleteThrows(error: Error) {
  const throwing = vi.spyOn(IDBObjectStore.prototype, "clear").mockImplementation(() => {
    throw error;
  });
  onTestFinished(() => throwing.mockRestore());
  return throwing;
}

test("Delete it now refused by the browser's storage says so, keeps the offer, and can be pressed again", async () => {
  await keptHere(Date.now() + 60_000);
  const screen = await render(<App />);
  const refusing = deleteThrows(new DOMException("Refused", "UnknownError"));
  await screen.getByRole("button", { name: "Delete it now" }).click();
  const said =
    "This browser wouldn't delete your changes to contract.pdf. Try again, or clear this site's data in the browser's settings.";
  await expect.element(screen.getByRole("status")).toHaveTextContent(said);
  await expect.element(screen.getByRole("button", { name: "Open it again" })).toBeVisible();
  refusing.mockRestore();
  await screen.getByRole("button", { name: "Delete it now" }).click();
  await expect.element(screen.getByRole("button", { name: "Open it again" })).not.toBeInTheDocument();
  await expect.element(screen.getByRole("status")).not.toHaveTextContent(said);
  await expect.poll(() => keptDocument()).toBeNull();
});

test("a bug while deleting is said as a bug, not as the browser refusing", async () => {
  const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
  await keptHere(Date.now() + 60_000);
  const screen = await render(<App />);
  deleteThrows(new TypeError("store is undefined"));
  await screen.getByRole("button", { name: "Delete it now" }).click();
  await expect.element(screen.getByRole("status")).toHaveTextContent(SOMETHING_WENT_WRONG);
  expect(logged).toHaveBeenCalledOnce();
});

test("Open it again waits while a delete is under way, so the delete can't take the work from the editor", async () => {
  await keptHere(Date.now() + 60_000);
  vi.mocked(forgetOrSayWhy).mockReturnValueOnce(new Promise(() => undefined));
  const screen = await render(<App />);
  await screen.getByRole("button", { name: "Delete it now" }).click();
  await screen.getByRole("button", { name: "Open it again" }).click();
  // Opened, the offer would go at once, in the same update.
  await expect.element(screen.getByRole("button", { name: "Open it again" })).toBeVisible();
  await expect.poll(() => keptDocument()).not.toBeNull();
});

test("one past the server's hour isn't offered, and is forgotten", async () => {
  await keptHere(Date.now() - 1);
  const screen = await render(<App />);
  await expect.element(screen.getByRole("button", { name: "Choose a PDF" })).toBeVisible();
  await expect.poll(() => keptDocument()).toBeNull();
  await expect.element(screen.getByRole("button", { name: "Open it again" })).not.toBeInTheDocument();
});
