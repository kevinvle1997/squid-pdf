import { beforeEach, expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";
import "./styles/tokens.css";
import "./styles/base.css";
import { App } from "./App";
import { upload } from "./api/client";
import { aDoc, aFont, aSpan } from "./fixtures";

vi.mock(import("./api/client"), async (original) => ({
  ...(await original()),
  upload: vi.fn(),
  stillThere: vi.fn(async () => true),
  pageImage: vi.fn(async () => undefined),
}));

beforeEach(() => {
  vi.mocked(upload).mockReset();
});

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
