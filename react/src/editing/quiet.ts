// The fonts whose warning an edit no longer shows in full, only the chip's short warning (Rule 1). Kept
// with the document's work, by the name a font is shown by, so quieting one quiets all its copies.
import { type Editor, spoken } from "./editor";
import { keepWork } from "./keeping";

/** Quiet the warning of the font shown as `name` as an edit starts, or bring it back: `quiet` says which. */
export function quietFont(editor: Editor, name: string, { quiet }: { quiet: boolean }): void {
  const quietFonts = new Set(editor.store.get().quietFonts);
  if (quiet) quietFonts.add(name);
  else quietFonts.delete(name);
  editor.store.set({
    quietFonts,
    said: spoken(quiet ? `Edits in ${name} won't warn again` : `Edits in ${name} warn again`),
  });
  // Kept only once the work is: an edit keeps it along with everything else.
  if (editor.keeping.file) void keepWork(editor);
}
