import { beforeEach, expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";
import "../../styles/tokens.css";
import "../../styles/base.css";
import { upload } from "../../api/client";
import { Drop } from "./Drop";

vi.mock(import("../../api/client"), async (original) => ({ ...(await original()), upload: vi.fn() }));

beforeEach(() => {
  vi.mocked(upload).mockReset();
});

test("a bug while opening is said, and another file can be chosen", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.mocked(upload).mockRejectedValue(new TypeError("request.response is null"));
  const screen = await render(<Drop onOpened={vi.fn()} onOpening={vi.fn()} />);
  const choose = screen.getByRole("button", { name: "Choose a PDF" });
  const input = screen.container.querySelector("input[type=file]") as HTMLInputElement;
  const file = new File(["%PDF-"], "contract.pdf", { type: "application/pdf" });
  const files = new DataTransfer();
  files.items.add(file);
  input.files = files.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
  await expect.element(screen.getByText("Something went wrong", { exact: false })).toBeVisible();
  await expect.element(choose).toBeEnabled();
});
