import { deleteFont, isGone, putFont } from "../api/client";
import type { Document, FontInfo, Sentences } from "../api/types";
import { sentenceOf } from "../bugs";
import { announcing, type Editor, type EditorState, type Layout, layoutOf } from "./editor";
import { addOwnFace, removeOwnFace } from "./faces";
import { missingIn } from "./fit";
import type { FontNotice } from "./notices";
import { fontOf } from "./spanFonts";
import { draftSpanOf } from "./typing";
import { fill } from "./words";
import { persistWork } from "./workPersistence";

export const FONT_FILES = ["font/ttf", "font/otf", ".ttf", ".otf"];

function isFontFile(file: { name: string; type: string }): boolean {
  const name = file.name.toLowerCase();
  return FONT_FILES.some((kind) => (kind.startsWith(".") ? name.endsWith(kind) : file.type === kind));
}

function shownNameOf(layout: Pick<Layout, "fonts">, fontName: string): string {
  const named = [...layout.fonts.values()].find((font) => font.name === fontName);
  return named?.shown_name ?? fontName;
}

export interface FontCopies {
  readonly shownName: string;
  readonly names: readonly string[];
  readonly font: FontInfo;
  readonly attached: boolean;
}

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

export function drawnByOf({ attached, font }: FontCopies, sentences: Sentences): { sentence: string; warn: boolean } {
  if (attached) return { sentence: sentences.drawn_in_your_copy, warn: false };
  if (font.substitute === null) return { sentence: sentences.drawn_in_own_copy, warn: false };
  const substituteSentence = font.same_widths ? sentences.substitute_same_widths : sentences.substitute;
  return { sentence: fill(substituteSentence, { font: font.substitute }), warn: true };
}

function copiesOf(layout: Layout, fontName: string): string[] {
  const shown = shownNameOf(layout, fontName);
  // Two fonts can share a name, and a copy goes in by the name: once is for both.
  const copies = [...layout.fonts.values()].filter((font) => font.shown_name === shown).map((font) => font.name);
  return [...new Set(copies)];
}

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
    const announcement = announcing(fill(doc.sentences.your_copy_in_use, { font: shownNameOf(layout, fontName) }));
    judgedAgain(editor, doc, { announcement, focusFont: fontName });
    void persistWork(editor);
  } catch (error) {
    for (const name of copies) if (editor.attached.get(name) !== file) removeOwnFace(name);
    refused(editor, fontName, error);
  }
}

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
  const announcement = announcing(fill(doc.sentences.your_copy_removed, { font: shownNameOf(layout, fontName) }));
  judgedAgain(editor, doc, { announcement, focusFont: fontName });
  void persistWork(editor);
}

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
      refusal = refusalOf(fontName, error);
    }
  }
  return { judged, refusal };
}

export function fontFocused(editor: Editor): void {
  editor.store.set({ focusFont: null });
}

/**
 * The font the field offers the user's copy of: one a letter typed in it is drawn by no copy of, in a line the first
 * such run's. None otherwise.
 */
export function copyOfferedFor(state: EditorState): string | null {
  const typing = draftSpanOf(state);
  // A form field draws it, not the page: no copy helps.
  if (typing === undefined || typing.span.form_field) return null;
  const { draft, span, font } = typing;
  const line = draft.runs === null ? undefined : state.layout.lines.get(draft.spanId);
  const runs = line?.spans.map((run, index) => ({ run, text: draft.runs?.[index] ?? "" })) ?? [
    { run: span, text: draft.text },
  ];
  const lacking = runs.find(({ run, text }) => {
    const runFont = run === span ? font : fontOf(state.layout.fonts, run);
    // The user's copy is in already: another can't help.
    return runFont !== undefined && !runFont.attached && missingIn(text, runFont.glyphs).length > 0;
  });
  return lacking?.run.font ?? null;
}

function judgedAgain(editor: Editor, doc: Document, also: Partial<EditorState>): void {
  const { store, queue } = editor;
  store.set({ doc, layout: layoutOf(doc), notices: { ...store.get().notices, font: null }, attaching: null, ...also });
  queue.redraw(store.get().reading);
}

function refused(editor: Editor, fontName: string, error: unknown): void {
  const refusal = refusalOf(fontName, error);
  const { notices } = editor.store.get();
  editor.store.set({
    notices: { ...notices, font: refusal },
    attaching: null,
    announcement: announcing(refusal.text),
  });
}

function refusalOf(fontName: string, error: unknown): FontNotice {
  return { tone: "warn", text: sentenceOf(error), font: fontName };
}

/** Only shown: the name the code knows a font by stays as the server sends it. */
export function readableName(name: string): string {
  return name
    .replace(/(PS|MT)(?=$|[-,])/g, "")
    .replace(/[-,_]+/g, " ")
    .replace(/(\p{Ll})(\p{Lu})/gu, "$1 $2")
    .replace(/\s+/g, " ")
    .trim();
}
