// The fonts whose warning an edit no longer shows in full, only the chip's short verdict (Rule 1). Kept
// with the document's work, by font name, since the warning is about this file's copy of the font.
import { type Editor, spoken } from "./editor";
import { shownName } from "./fonts";
import { keepWork } from "./keeping";

/** Whether an edit in `fontName` shows its warning in full as typing starts: false quiets it, true brings it back. */
export function warnAtEdit(editor: Editor, fontName: string, warns: boolean): void {
  const quiet = new Set(editor.store.get().quietFonts);
  if (warns) quiet.delete(fontName);
  else quiet.add(fontName);
  const name = shownName(fontName);
  editor.store.set({
    quietFonts: quiet,
    said: spoken(warns ? `Edits in ${name} warn again` : `Edits in ${name} won't warn again`),
  });
  // Kept only once the work is: an edit keeps it along with everything else.
  if (editor.keeping.file) void keepWork(editor);
}
