import { type FocusEvent, type KeyboardEvent, useRef } from "react";
import { Input, TextField } from "react-aria-components";
import type { PageInfo, SpanInfo } from "../../api/types";
import { Num } from "../../ui/Num";
import { Warn } from "../../ui/Warn";
import { say } from "../editor";
import { finish, returnTo, type as typeInto } from "../typing";
import { boxOf, points, useEditor, useEditorState } from "./context";
import styles from "./EditField.module.css";
import { familyOf, previewFaceOf } from "../faces";
import { type Fit, fitOf, troublesOf, widthPt } from "../fit";

const SIZE = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });

/** The index in `text` of the letter at `atPt` points from its start. */
function letterAt(text: string, atPt: number, glyphs: Record<string, number>, size: number): number {
  const letters = [...text];
  for (let index = 0; index < letters.length; index++) {
    if (widthPt(letters.slice(0, index + 1).join(""), glyphs, size) > atPt) return index;
  }
  return letters.length;
}

/** The word around `index`: what a double press selects. */
function wordAround(text: string, index: number): [number, number] {
  const isWord = (letter: string | undefined) => letter !== undefined && /\S/.test(letter);
  let start = index;
  let end = index;
  while (isWord(text[start - 1])) start--;
  while (isWord(text[end])) end++;
  return [start, end];
}

/**
 * Typing in place: the span's text in the face that will draw it, checked as it's typed.
 * It stands where the span's mark stood, so Tab and Shift+Tab go on to the next span unaided.
 * The editor holds what's typed, so an export mid-word takes it too.
 */
export function EditField({ span, info }: { span: SpanInfo; info: PageInfo }) {
  const editor = useEditor();
  const text = useEditorState((state) => state.draft?.text ?? "");
  const rules = useEditorState((state) => state.doc.fit);
  const copy = useEditorState((state) => state.doc.copy);
  const font = useEditorState((state) => state.layout.fonts.get(span.font));
  const start = useEditorState((state) => state.view.spans.get(span.id)?.text) ?? span.text;
  const face = font === undefined ? "Liberation Serif Regular" : previewFaceOf(font);
  const placed = useRef(false);
  const glyphs = font?.glyphs ?? {};

  const fitFor = (typed: string) => (font === undefined ? null : fitOf(span, font, typed, rules));
  const troublesIn = (fit: Fit | null) => (fit === null ? [] : troublesOf(fit, rules, copy, font?.substitute ?? face));
  const said = troublesIn(fitFor(text)).join("; ");

  // Once, as the field takes focus: the caret goes where the press was, the word under it,
  // or everything from the keyboard. Later focus keeps the caret where the user put it.
  function place(event: FocusEvent<HTMLInputElement>) {
    if (placed.current) return;
    placed.current = true;
    const field = event.currentTarget;
    const at = editor.store.get().draft?.atPt;
    if (at == null) field.select();
    else field.setSelectionRange(...wordAround(start, letterAt(start, at, glyphs, span.size)));
    if (said !== "") say(editor, said);
  }

  // A trouble is announced as it appears or changes, not as its numbers tick by with each letter.
  function type(next: string) {
    const kind = (fit: Fit | null) => (fit === null ? "" : `${fit.missing.join("")} ${fit.deltaPt > rules.tolerance_pt}`);
    const now = fitFor(next);
    if (kind(now) !== kind(fitFor(text))) {
      const troubles = troublesIn(now);
      if (troubles.length > 0) say(editor, troubles.join("; "));
    }
    typeInto(editor, next);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter" && event.key !== "Escape") return;
    event.preventDefault();
    // Back on the span, but its note would cover what was just typed: it waits for the next visit.
    returnTo(editor, span.id);
    finish(editor, event.key === "Enter");
  }

  const box = boxOf(span.bbox, info);
  const [red = 0, green = 0, blue = 0] = span.color;
  // Half an em spare: the preview face's widths are close to the server's, not always equal.
  const wide = Math.max(span.bbox.x1 - span.bbox.x0, widthPt(text, glyphs, span.size) + span.size / 2);
  return (
    <>
      <TextField aria-label={`Change “${start}”`} value={text} onChange={type} className={styles.field}>
        <Input
          autoFocus
          onFocus={place}
          className={said !== "" ? styles.troubleInput : styles.input}
          spellCheck={false}
          autoComplete="off"
          onKeyDown={onKeyDown}
          onBlur={() => finish(editor, true)}
          style={{
            left: box.left,
            top: box.top,
            height: box.height,
            width: points(wide, info),
            fontSize: points(span.size, info),
            fontFamily: familyOf(face),
            color: `rgb(${red * 255} ${green * 255} ${blue * 255})`,
          }}
        />
      </TextField>
      <div className={styles.chip} aria-hidden="true" style={{ left: box.left, top: points(span.bbox.y1 + 4, info) }}>
        {said !== "" ? (
          <span className={styles.bad}>
            <Warn>{said}</Warn>
          </span>
        ) : (
          <>
            <span>{face}</span>
            <Num>{SIZE.format(span.size)} pt</Num>
          </>
        )}
      </div>
    </>
  );
}
