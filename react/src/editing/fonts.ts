// The user's own copies of the document's fonts, attached through the reopener and held beside the PDF.
// The document comes back judged again, so marks and fits change in place, with no line under the bar (Rule 6).
import { deleteFont, isGone, putFont } from "../api/client";
import type { Document, FontInfo, Sentences } from "../api/types";
import { sentenceOf } from "../bugs";
import { announcing, type Editor, type EditorState, type Layout, layoutOf } from "./editor";
import { addOwnFace, removeOwnFace } from "./faces";
import { missingIn } from "./fit";
import type { FontNotice } from "./notices";
import { draftSpanOf } from "./typing";
import { fill } from "./words";
import { persistWork } from "./workPersistence";

/** The font files the user's copy can be: TrueType or OpenType, by type or by name. */
export const FONT_FILES = ["font/ttf", "font/otf", ".ttf", ".otf"];

/** Whether a file dropped or picked is one of FONT_FILES: its type, or else its name's ending. */
function isFontFile(file: { name: string; type: string }): boolean {
  const name = file.name.toLowerCase();
  return FONT_FILES.some((kind) => (kind.startsWith(".") ? name.endsWith(kind) : file.type === kind));
}

/** The name the reader knows `fontName` by, as the server says it: copies of one font share it. */
function shownNameOf(layout: Pick<Layout, "fonts">, fontName: string): string {
  const named = [...layout.fonts.values()].find((font) => font.name === fontName);
  // A font the document doesn't list goes by its own name.
  return named?.shown_name ?? fontName;
}

/** The document's fonts shown under one name: the copies of one font, a row of the Fonts list. */
export interface FontCopies {
  readonly shownName: string;
  readonly names: readonly string[]; // each copy's own name, as the server lists it
  readonly font: FontInfo; // the copy the row speaks for: one a similar font draws, if any
  readonly attached: boolean; // the user's copy is in for every one of them
}

/** The document's fonts, one entry per name shown, in the order each name first comes. */
export function copiesByName(fonts: readonly FontInfo[]): FontCopies[] {
  const byName = new Map<string, [FontInfo, ...FontInfo[]]>();
  for (const font of fonts) {
    const copies = byName.get(font.shown_name);
    if (copies === undefined) byName.set(font.shown_name, [font]);
    else copies.push(font);
  }
  return [...byName].map(([shownName, copies]) => ({
    shownName,
    names: copies.map((font) => font.name),
    font: copies.find((font) => font.substitute !== null) ?? copies[0],
    attached: copies.every((font) => font.attached),
  }));
}

/** What draws a row's font, in the server's words: the user's copy, the file's own, or a similar font, warned. */
export function drawnByOf({ attached, font }: FontCopies, sentences: Sentences): { sentence: string; warn: boolean } {
  if (attached) return { sentence: sentences.drawn_in_your_copy, warn: false };
  if (font.substitute === null) return { sentence: sentences.drawn_in_own_copy, warn: false };
  const substituteSentence = font.same_widths ? sentences.substitute_same_widths : sentences.substitute;
  return { sentence: fill(substituteSentence, { font: font.substitute }), warn: true };
}

/** Every font listed under the name `fontName` is shown by: its copies, itself among them, each name once. */
function copiesOf(layout: Layout, fontName: string): string[] {
  const shown = shownNameOf(layout, fontName);
  // Two fonts can share a name, and a copy goes in by the name: once is for both.
  const copies = [...layout.fonts.values()].filter((font) => font.shown_name === shown).map((font) => font.name);
  return [...new Set(copies)];
}

/**
 * Attach `file` as the user's copy of `fontName` and every copy shown under its name, one font at a
 * time; a refusal says why until the next change. Type3 copies are each a font of their own to the server.
 */
export async function attachFont(editor: Editor, fontName: string, file: Blob): Promise<void> {
  if (editor.store.get().attaching !== null) return;
  editor.store.set({ attaching: fontName });
  const { layout } = editor.store.get();
  const copies = copiesOf(layout, fontName);
  // Read for the preview while the server checks it, so the preview draws in it as the server takes it.
  const preview = Promise.all(copies.map((name) => addOwnFace(name, file)));
  try {
    let doc = editor.store.get().doc;
    for (const name of copies) {
      doc = await editor.reopener.withDocument((current) => putFont(current.id, name, file));
      editor.attached.set(name, file);
    }
    await preview;
    const announcement = announcing(`Your copy of ${shownNameOf(layout, fontName)} is in use`);
    judgedAgain(editor, doc, { announcement, focusFont: fontName });
    void persistWork(editor);
  } catch (error) {
    // A copy put in before the refusal stays, as the server has it; the rest preview no more.
    for (const name of copies) if (editor.attached.get(name) !== file) removeOwnFace(name);
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

/** Remove the user's copy of `fontName` and of every copy shown under its name: the document is judged without it. */
export async function detachFont(editor: Editor, fontName: string): Promise<void> {
  if (editor.store.get().attaching !== null) return;
  editor.store.set({ attaching: fontName });
  const { layout } = editor.store.get();
  const copies = copiesOf(layout, fontName).filter((name) => editor.attached.has(name));
  let doc = editor.store.get().doc;
  for (const name of copies) {
    // Forgotten first, so a document opened again meanwhile doesn't get it back; refused, it's kept.
    const file = editor.attached.get(name);
    editor.attached.delete(name);
    try {
      doc = await editor.reopener.withDocument((current) => deleteFont(current.id, name));
    } catch (error) {
      if (file !== undefined) editor.attached.set(name, file);
      refused(editor, fontName, error);
      return;
    }
    removeOwnFace(name);
  }
  const announcement = announcing(`Your copy of ${shownNameOf(layout, fontName)} is removed`);
  judgedAgain(editor, doc, { announcement, focusFont: fontName });
  void persistWork(editor);
}

/**
 * The document opened again as `doc`, with each copy the user gave attached to it again, and
 * the last refusal, if one wasn't. Gone again meanwhile, that's thrown: it's the reopen's failure.
 */
export async function reattach(
  editor: Editor,
  doc: Document,
): Promise<{ judged: Document; refusal: FontNotice | null }> {
  let judged = doc;
  let refusal: FontNotice | null = null;
  for (const [fontName, file] of editor.attached) {
    try {
      judged = await putFont(judged.id, fontName, file);
    } catch (error) {
      if (isGone(error)) throw error;
      // A copy the new document refuses lends nothing there: said with the reopen, and the edits still apply.
      refusal = refusalOf(fontName, error);
    }
  }
  return { judged, refusal };
}

/** Focus has reached the fonts list's button it was sent to. */
export function fontFocused(editor: Editor): void {
  editor.store.set({ focusFont: null });
}

/** Whether the field offers the user's copy: a letter typed is one no copy of the font draws. */
export function offersCopy(state: EditorState): boolean {
  const typing = draftSpanOf(state);
  // A form field draws it, or the user's copy is in already: another can't help.
  if (typing === undefined || typing.span.form_field || typing.font.attached) return false;
  return missingIn(typing.draft.text, typing.font.glyphs).length > 0;
}

/** The server judged every span again: its marks and fits take over, and every edited page is drawn again. */
function judgedAgain(editor: Editor, doc: Document, also: Partial<EditorState>): void {
  const { store, queue } = editor;
  store.set({ doc, layout: layoutOf(doc), notices: { ...store.get().notices, font: null }, attaching: null, ...also });
  queue.redraw(store.get().reading);
}

/** The server refused what was asked of `fontName`'s copy, or nothing answered: shown and heard until the next change. */
function refused(editor: Editor, fontName: string, error: unknown): void {
  const refusal = refusalOf(fontName, error);
  const { notices } = editor.store.get();
  editor.store.set({
    notices: { ...notices, font: refusal },
    attaching: null,
    announcement: announcing(refusal.text),
  });
}

/** Why `fontName`'s copy was refused, in the server's words, said beside that font too. */
function refusalOf(fontName: string, error: unknown): FontNotice {
  return { tone: "warn", text: sentenceOf(error), font: fontName };
}

/**
 * A font's name as a reader writes it, for a label: PostScript's dashes and commas spaced, its words run
 * together set apart, and its PS and MT marks dropped. "TimesNewRomanPS-BoldMT" reads "Times New Roman Bold".
 * Only shown: the name the code knows a font by stays as the server sends it.
 */
export function readableName(name: string): string {
  return name
    .replace(/(PS|MT)(?=$|[-,])/g, "")
    .replace(/[-,_]+/g, " ")
    .replace(/(\p{Ll})(\p{Lu})/gu, "$1 $2")
    .replace(/\s+/g, " ")
    .trim();
}
