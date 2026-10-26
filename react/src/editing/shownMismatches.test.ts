import { expect, test } from "vitest";
import { aDoc, aFont, aSpan } from "../fixtures";
import { createEditor } from "./editor";
import { toggleShownMismatches } from "./shownMismatches";

test("an eye turned on marks its row where its places are, moving nothing; pressed again, it stops", () => {
  const doc = aDoc({
    spans: [aSpan({ id: "a", font: "Arial", substitute: "Liberation Sans" })],
    fonts: [aFont("Arial")],
  });
  const editor = createEditor(new File(["%PDF-"], "contract.pdf"), doc, 2);

  toggleShownMismatches(editor, { key: "font:Arial" });
  expect([...editor.store.get().shownMismatches]).toEqual(["font:Arial"]);
  expect(editor.store.get().scrollTo).toBeNull();

  toggleShownMismatches(editor, { key: "font:Arial" });
  expect(editor.store.get().shownMismatches.size).toBe(0);
});
