// A PDF dropped on the editor opens in place of this one, unless that would lose what this browser alone
// holds. The browser never gets the drop, which would open the file in this tab instead.
import { announcing, type Editor } from "./editor";
import { unexported } from "./export";
import { warn } from "./notices";

/** Whether a drag carries files, as a drop from the desktop does, not text or a link. */
export function carriesFiles(data: Pick<DataTransfer, "types"> | null): boolean {
  return data?.types.includes("Files") ?? false;
}

/** Whether a dropped file is a PDF: its type, or else its name's ending. */
function isPdf(file: File): boolean {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

/** The PDF to open from what was dropped, or null, with the reason shown under the bar and heard. */
export function pdfDropped(editor: Editor, files: readonly File[]): File | null {
  const pdf = files.find(isPdf);
  if (pdf === undefined) {
    return refused(
      editor,
      "Only a PDF can be dropped here. To use your copy of a font, drop it on the text you're changing, or choose it in Fonts.",
    );
  }
  // Opening it would lose what this browser alone holds.
  if (unexported(editor.store.get())) {
    return refused(editor, "Export your changes first, or undo them: opening another PDF here would lose them.");
  }
  return pdf;
}

/** Nothing opens: why, shown under the bar and heard. */
function refused(editor: Editor, why: string): null {
  const { notices } = editor.store.get();
  editor.store.set({ notices: { ...notices, drop: warn(why) }, announcement: announcing(why) });
  return null;
}
