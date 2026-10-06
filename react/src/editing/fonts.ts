// The user's own copies of the document's fonts, attached through the reopener and kept beside the PDF.
// The document comes back judged again, so marks and fits change in place, unannounced (Rule 6).
import { deleteFont, ProblemError, putFont } from "../api/client";
import type { Document } from "../api/types";
import { reportBug } from "../bugs";
import { type Editor, type EditorState, layoutOf, spoken } from "./editor";
import { missingIn } from "./fit";

const SUBSET_PREFIX = /^[A-Z]{6}\+/; // a trimmed copy's name starts ABCDEF+

/** The font files the user's copy can be: TrueType or OpenType, by type or by name. */
export const FONT_FILES = ["font/ttf", "font/otf", ".ttf", ".otf"];

/** Whether a file dropped or picked is one of FONT_FILES: its type, or else its name's ending. */
export function isFontFile(file: { name: string; type: string }): boolean {
  const name = file.name.toLowerCase();
  return FONT_FILES.some((kind) => (kind.startsWith(".") ? name.endsWith(kind) : file.type === kind));
}

/** A font's name as the reader knows it, without a trimmed copy's prefix. */
export function shownName(fontName: string): string {
  return fontName.replace(SUBSET_PREFIX, "");
}

/** Attach `file` as the user's copy of `fontName`; one at a time, and a refusal says why until the next change. */
export async function attachFont(editor: Editor, fontName: string, file: Blob): Promise<void> {
  if (editor.store.get().attaching !== null) return;
  editor.store.set({ attaching: fontName });
  try {
    const doc = await editor.reopener.withDocument((current) => putFont(current.id, fontName, file));
    editor.attached.set(fontName, file);
    judgedAgain(editor, doc, { said: spoken(`Your copy of ${shownName(fontName)} is in use`), focusFont: fontName });
  } catch (error) {
    refused(editor, fontName, error);
  }
}

/** Attach the font file among what was dropped; anything else goes too, for the server to say what it is. */
export async function attachDropped(
  editor: Editor,
  fontName: string,
  dropped: readonly Promise<File>[],
): Promise<void> {
  try {
    const files = await Promise.all(dropped);
    const file = files.find(isFontFile) ?? files[0];
    if (file !== undefined) await attachFont(editor, fontName, file);
  } catch (error) {
    refused(editor, fontName, error);
  }
}

/** Remove the user's copy of `fontName`: the document is judged without it. */
export async function detachFont(editor: Editor, fontName: string): Promise<void> {
  if (editor.store.get().attaching !== null) return;
  editor.store.set({ attaching: fontName });
  // Forgotten first, so a document opened again meanwhile doesn't get it back; refused, it's kept.
  const file = editor.attached.get(fontName);
  editor.attached.delete(fontName);
  try {
    const doc = await editor.reopener.withDocument((current) => deleteFont(current.id, fontName));
    judgedAgain(editor, doc, { said: spoken(`Your copy of ${shownName(fontName)} is removed`), focusFont: fontName });
  } catch (error) {
    if (file !== undefined) editor.attached.set(fontName, file);
    refused(editor, fontName, error);
  }
}

/** The document opened again as `doc`: each copy the user gave is attached to it again. */
export async function reattach(editor: Editor, doc: Document): Promise<void> {
  let judged = doc;
  for (const [fontName, file] of editor.attached) {
    try {
      judged = await putFont(judged.id, fontName, file);
    } catch (error) {
      // A copy the new document refuses lends nothing there: said, and the edits still apply.
      refused(editor, fontName, error);
    }
  }
  // A refusal above is still said.
  if (judged !== doc) judgedAgain(editor, judged, { notices: editor.store.get().notices });
}

/** Focus has reached the fonts list's button it was sent to. */
export function fontFocused(editor: Editor): void {
  editor.store.set({ focusFont: null });
}

/** Whether the field offers the user's copy: a letter typed is one no copy of the font draws. */
export function offersCopy(state: EditorState): boolean {
  const { draft, layout } = state;
  const span = draft === null ? undefined : layout.spans.get(draft.spanId);
  const font = span === undefined ? undefined : layout.fonts.get(span.font);
  // A form field draws it, or the user's copy is in already: another can't help.
  if (draft === null || span === undefined || font === undefined || span.form_field || font.attached) return false;
  return missingIn(draft.text, font.glyphs).length > 0;
}

/** The server judged every span again: its marks and fits take over, and every edited page is drawn again. */
function judgedAgain(editor: Editor, doc: Document, also: Partial<EditorState>): void {
  const { store, queue } = editor;
  store.set({ doc, layout: layoutOf(doc), notices: { ...store.get().notices, font: null }, attaching: null, ...also });
  queue.redraw(store.get().reading);
}

/** The server refused what was asked of `fontName`'s copy, or nothing answered: shown and heard until the next change. */
function refused(editor: Editor, fontName: string, error: unknown): void {
  const text = error instanceof ProblemError ? error.problem.detail : reportBug(error);
  const { notices } = editor.store.get();
  editor.store.set({
    notices: { ...notices, font: { tone: "warn", text, font: fontName } },
    attaching: null,
    said: spoken(text),
  });
}
