import { expect, test } from "vitest";
import { aDoc, aFont, aSpan } from "../fixtures";
import { createEditor } from "./editor";
import { toggleShown } from "./shown";

test("an eye turned on marks its row where its places are, moving nothing; pressed again, it stops", () => {
  const doc = aDoc({
    spans: [aSpan({ id: "a", font: "Arial", substitute: "Liberation Sans" })],
    fonts: [aFont("Arial")],
  });
  const editor = createEditor(new File(["%PDF-"], "contract.pdf"), doc, 2);

  toggleShown(editor, { key: "font:Arial" });
  expect([...editor.store.get().shownUnlike]).toEqual(["font:Arial"]);
  expect(editor.store.get().scrollTo).toBeNull();

  toggleShown(editor, { key: "font:Arial" });
  expect(editor.store.get().shownUnlike.size).toBe(0);
});
