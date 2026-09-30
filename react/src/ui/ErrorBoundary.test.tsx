import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";
import { ErrorBoundary } from "./ErrorBoundary";

function Broken(): never {
  throw new TypeError("cannot read properties of undefined");
}

test("what throws while drawing shows the fallback, not a blank page", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const screen = await render(
    <ErrorBoundary fallback={<p>Something went wrong.</p>}>
      <Broken />
    </ErrorBoundary>,
  );
  await expect.element(screen.getByText("Something went wrong.")).toBeVisible();
});
