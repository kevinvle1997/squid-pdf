// The browser never gets the drop, which would open the file in this tab instead.
import { announcing, type Editor } from "./editor";
import { unexported } from "./export";
import { warn } from "./notices";

export function carriesFiles(data: Pick<DataTransfer, "types"> | null): boolean {
  return data?.types.includes("Files") ?? false;
}

function isPdf(file: File): boolean {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

export function pdfDropped(editor: Editor, files: readonly File[]): File | null {
  const pdf = files.find(isPdf);
  if (pdf === undefined) {
    return refused(
      editor,
      "Only a PDF can be dropped here. To use your copy of a font, drop it on the text you're changing, or choose it in Fonts.",
    );
  }
  if (unexported(editor.store.get())) {
    return refused(editor, "Export your changes first, or undo them: opening another PDF here would lose them.");
  }
  return pdf;
}

function refused(editor: Editor, why: string): null {
  const { notices } = editor.store.get();
  editor.store.set({ notices: { ...notices, drop: warn(why) }, announcement: announcing(why) });
  return null;
}
