import { type FocusEvent, type KeyboardEvent, useRef } from "react";
import { Input, TextField } from "react-aria-components";
import type { PageInfo, SpanInfo } from "../../api/types";
import { Num } from "../../ui/Num";
import { Warn } from "../../ui/Warn";
import { letterAt, wordAround } from "../caret";
import { say } from "../editor";
import { previewFaceOf } from "../faces";
import { widthPt } from "../fit";
import { finish, returnTo, troublesIn, typeInto } from "../typing";
import { useEditor, useEditorState } from "./context";
import styles from "./EditField.module.css";
import { boxOf, points, spanTextStyle } from "./geometry";

const SIZE = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });

/**
 * Typing in place: the span's text in the face that will draw it, checked as it's typed.
 * It stands where the span's mark stood, so Tab and Shift+Tab go on to the next span unaided.
 * The editor holds what's typed, so an export mid-word takes it too.
 */
export function EditField({ span, info }: { span: SpanInfo; info: PageInfo }) {
  const editor = useEditor();
  const text = useEditorState((state) => state.draft?.text ?? "");
  const font = useEditorState((state) => state.layout.fonts.get(span.font));
  const start = useEditorState((state) => state.reading.spans.get(span.id)?.text) ?? span.text;
  const face = font === undefined ? "Liberation Serif Regular" : previewFaceOf(font);
  const placed = useRef(false);
  const glyphs = font?.glyphs ?? {};

  const trouble = useEditorState((state) => troublesIn(state, state.draft?.text ?? "").said.join("; "));

  // Once, as the field takes focus: the caret goes where the press was, the word under it,
  // or everything from the keyboard. Later focus keeps the caret where the user put it.
  function place(event: FocusEvent<HTMLInputElement>) {
    if (placed.current) return;
    placed.current = true;
    const field = event.currentTarget;
    const at = editor.store.get().draft?.atPt;
    if (at == null) field.select();
    else field.setSelectionRange(...wordAround(start, letterAt(start, at, glyphs, span.size)));
    if (trouble !== "") say(editor, trouble);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter" && event.key !== "Escape") return;
    event.preventDefault();
    // Back on the span, but its note would cover what was just typed: it waits for the next visit.
    returnTo(editor, span.id);
    finish(editor, event.key === "Enter");
  }

  const box = boxOf(span.bbox, info);
  // Half an em spare: the preview face's widths are close to the server's, not always equal.
  const wide = Math.max(span.bbox.x1 - span.bbox.x0, widthPt(text, glyphs, span.size) + span.size / 2);
  return (
    <>
      <TextField
        aria-label={`Change “${start}”`}
        value={text}
        onChange={(next) => typeInto(editor, next)}
        className={styles.field}
      >
        <Input
          autoFocus
          onFocus={place}
          className={trouble !== "" ? styles.troubleInput : styles.input}
          spellCheck={false}
          autoComplete="off"
          onKeyDown={onKeyDown}
          onBlur={() => finish(editor, true)}
          style={{ ...spanTextStyle(span, info, face), width: points(wide, info) }}
        />
      </TextField>
      <div className={styles.chip} aria-hidden="true" style={{ left: box.left, top: points(span.bbox.y1 + 4, info) }}>
        {trouble !== "" ? (
          <span className={styles.bad}>
            <Warn>{trouble}</Warn>
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
