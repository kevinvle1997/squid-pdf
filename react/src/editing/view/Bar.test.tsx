import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";
import "../../styles/tokens.css";
import "../../styles/base.css";
import { A4, aDoc } from "../../fixtures";
import { createEditor } from "../editor";
import { pageInView } from "../zoom";
import { Bar } from "./Bar";
import { EditorContext } from "./context";

// The bar draws its Fonts button each time it's drawn itself, so counting Fonts counts the bar.
const drawn = vi.hoisted(() => ({ fonts: 0 }));
vi.mock(import("./Fonts"), async (original) => {
  const module = await original();
  return {
    ...module,
    Fonts: () => {
      drawn.fonts++;
      return module.Fonts();
    },
  };
});

test("a page scrolled into view redraws where the bar says the reader is, and nothing else of the bar", async () => {
  const editor = createEditor(new File(["%PDF-"], "contract.pdf"), aDoc({ pages: [A4, A4, A4] }), 2);
  const screen = await render(
    <EditorContext.Provider value={editor}>
      <Bar />
    </EditorContext.Provider>,
  );
  await expect.element(screen.getByText("Page 1 of 3")).toBeInTheDocument();
  const before = drawn.fonts;
  pageInView(editor, 2);
  await expect.element(screen.getByText("Page 3 of 3")).toBeInTheDocument();
  expect(drawn.fonts).toBe(before);
});
