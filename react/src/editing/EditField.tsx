import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { Input, TextField } from "react-aria-components";
import type { PageInfo, SpanInfo } from "../api/types";
import { boxOf, focusSpan, markId, points, useEditor } from "./context";
import styles from "./EditField.module.css";
import { previewFaceOf, useFace } from "./faces";
import { fitOf, troublesOf, widthPt } from "./fit";

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

/** Typing in place: the span's text in the face that will draw it, checked as it's typed. */
export function EditField({ span, info }: { span: SpanInfo; info: PageInfo }) {
  const editor = useEditor();
  const font = editor.fonts.get(span.font);
  const start = editor.latest.get(span.id) ?? span.text;
  const [text, setText] = useState(start);
  const face = font === undefined ? "Liberation Serif Regular" : previewFaceOf(font);
  const family = useFace(face);
  const input = useRef<HTMLInputElement>(null);
  const finished = useRef(false);
  const glyphs = font?.glyphs ?? {};

  const fit = font === undefined ? null : fitOf(span, font, text, editor.doc.fit);
  const troubles = fit === null ? [] : troublesOf(fit, editor.doc.fit, editor.doc.copy, font?.substitute ?? face);
  const said = troubles.join("; ");

  // Put the caret where the press was: the word under it, or everything from the keyboard.
  useEffect(() => {
    const field = input.current;
    if (field === null) return;
    field.focus();
    const at = editor.editing?.atPt;
    if (at == null) return field.select();
    const [from, to] = wordAround(start, letterAt(start, at, glyphs, span.size));
    field.setSelectionRange(from, to);
    // Only when editing starts: later renders keep the caret where the user put it.
  }, []);

  // A trouble is announced once when it appears, not on every keystroke.
  const say = editor.say;
  useEffect(() => {
    if (said !== "") say(said);
  }, [said, say]);

  function finish(keep: boolean, then?: () => void) {
    if (finished.current) return;
    finished.current = true;
    if (keep) editor.commit(span.id, text);
    else editor.stopEditing();
    then?.();
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === "Escape") {
      event.preventDefault();
      // Back on the span, but its note would cover what was just typed: it waits for the next visit.
      editor.returnTo(span.id);
      finish(event.key === "Enter", () => requestAnimationFrame(() => focusSpan(span.id)));
      return;
    }
    // Tab goes on to the next span, as it does between marks.
    if (event.key === "Tab") {
      event.preventDefault();
      const marks = [...document.querySelectorAll<HTMLElement>("[id^='span-']")];
      const here = marks.findIndex((mark) => mark.id === markId(span.id));
      const next = marks[here + (event.shiftKey ? -1 : 1)];
      finish(true, () => requestAnimationFrame(() => next?.focus()));
    }
  }

  const box = boxOf(span.bbox, info);
  const [red = 0, green = 0, blue = 0] = span.color;
  // Half an em spare: the preview face's widths are close to the server's, not always equal.
  const wide = Math.max(span.bbox.x1 - span.bbox.x0, widthPt(text, glyphs, span.size) + span.size / 2);
  return (
    <>
      <TextField aria-label={`Change “${start}”`} value={text} onChange={setText} className={styles.field}>
        <Input
          ref={input}
          className={troubles.length > 0 ? styles.troubleInput : styles.input}
          spellCheck={false}
          autoComplete="off"
          onKeyDown={onKeyDown}
          onBlur={() => finish(true)}
          style={{
            left: box.left,
            top: box.top,
            height: box.height,
            width: points(wide, info),
            fontSize: points(span.size, info),
            fontFamily: family === null ? "serif" : `"${family}", serif`,
            color: `rgb(${red * 255} ${green * 255} ${blue * 255})`,
          }}
        />
      </TextField>
      <div className={styles.chip} aria-hidden="true" style={{ left: box.left, top: points(span.bbox.y1 + 4, info) }}>
        {troubles.length > 0 ? (
          <span className={styles.bad}>{said}</span>
        ) : (
          <>
            <span>{face}</span>
            <span className={styles.num}>{SIZE.format(span.size)} pt</span>
          </>
        )}
      </div>
    </>
  );
}
