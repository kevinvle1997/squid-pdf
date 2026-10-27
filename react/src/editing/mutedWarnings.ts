// Fonts whose warning an edit no longer shows in full, only the chip's short warning (Rule 1). Keyed by
// the name a font is shown by, so muting one mutes all its copies.
import { announcing, type Editor } from "./editor";
import { fill } from "./words";
import { persistWork } from "./workPersistence";

/** Mute the warning of the font shown as `name` as an edit starts, or bring it back: `muted` says which. */
export function setWarningMuted(editor: Editor, name: string, { muted }: { muted: boolean }): void {
  const { mutedFonts: before, doc } = editor.store.get();
  const mutedFonts = new Set(before);
  if (muted) mutedFonts.add(name);
  else mutedFonts.delete(name);
  editor.store.set({
    mutedFonts,
    announcement: announcing(fill(muted ? doc.sentences.warning_muted : doc.sentences.warning_unmuted, { font: name })),
  });
  // Persisted only once the work is: the next edit persists it along with everything else.
  if (editor.persistence.file) void persistWork(editor);
}
