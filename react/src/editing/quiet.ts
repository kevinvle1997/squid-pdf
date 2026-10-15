// The fonts whose warning an edit no longer shows in full, only the chip's short warning (Rule 1). Kept
// with the document's work, by font name, since the warning is about this file's copy of the font.
import { type Editor, spoken } from "./editor";
import { shownName } from "./fonts";
import { keepWork } from "./keeping";

/** Quiet `fontName`'s warning as an edit starts, or bring it back: `quiet` says which. */
export function quietFont(editor: Editor, fontName: string, { quiet }: { quiet: boolean }): void {
  const quietFonts = new Set(editor.store.get().quietFonts);
  if (quiet) quietFonts.add(fontName);
  else quietFonts.delete(fontName);
  const name = shownName(fontName);
  editor.store.set({
    quietFonts,
    said: spoken(quiet ? `Edits in ${name} won't warn again` : `Edits in ${name} warn again`),
  });
  // Kept only once the work is: an edit keeps it along with everything else.
  if (editor.keeping.file) void keepWork(editor);
}
