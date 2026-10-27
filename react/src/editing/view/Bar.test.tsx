import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";
import "../../styles/tokens.css";
import "../../styles/base.css";
import { A4, aDoc, aFont, aSpan, PSEUDO_SENTENCES } from "../../fixtures";
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

test("before any change, the count is the server's sentence, warned whole", async () => {
  const similar = aSpan({
    id: "similar",
    font: "Named",
    fidelity: "substitute",
    substitute: "Liberation Serif Regular",
  });
  const doc = aDoc({
    sentences: PSEUDO_SENTENCES,
    spans: [similar],
    fonts: [aFont("Named", { substitute: "Liberation Serif Regular" })],
  });
  const editor = createEditor(new File(["%PDF-"], "contract.pdf"), doc, 2);
  const screen = await render(
    <EditorContext.Provider value={editor}>
      <Bar />
    </EditorContext.Provider>,
  );
  const count = screen.getByRole("button", { name: "EDITS IN 1 PLACE WILL USE A SIMILAR FONT" });
  await expect.element(count).toBeVisible();
  // The warning is the whole sentence, as Find's is: never a number marked inside words that aren't.
  await expect.element(count.getByText("EDITS IN 1 PLACE WILL USE A SIMILAR FONT", { exact: true })).toBeVisible();
});
