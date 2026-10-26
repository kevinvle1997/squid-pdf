// Fonts whose warning an edit no longer shows in full, only the chip's short warning (Rule 1). Keyed by
// the name a font is shown by, so muting one mutes all its copies.
import { announcing, type Editor } from "./editor";
import { persistWork } from "./workPersistence";

/** Mute the warning of the font shown as `name` as an edit starts, or bring it back: `muted` says which. */
export function setWarningMuted(editor: Editor, name: string, { muted }: { muted: boolean }): void {
  const mutedFonts = new Set(editor.store.get().mutedFonts);
  if (muted) mutedFonts.add(name);
  else mutedFonts.delete(name);
  editor.store.set({
    mutedFonts,
    announcement: announcing(muted ? `Edits in ${name} won't warn again` : `Edits in ${name} warn again`),
  });
  // Persisted only once the work is: the next edit persists it along with everything else.
  if (editor.persistence.file) void persistWork(editor);
}
