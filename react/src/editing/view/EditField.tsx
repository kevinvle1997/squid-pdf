import {
  type DragEvent,
  type DragEventHandler,
  type FocusEvent,
  type KeyboardEvent,
  useCallback,
  useRef,
  useState,
} from "react";
import { isFileDropItem, useDrop } from "react-aria";
import { FileTrigger, Input, TextField } from "react-aria-components";
import type { PageInfo, SpanInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { Num } from "../../ui/Num";
import { Warn } from "../../ui/Warn";
import { letterAt, wordAround } from "../caret";
import { say } from "../editor";
import { DEFAULT_FACE, previewFaceOf } from "../faces";
import { widthPt } from "../fit";
import { attachDropped, attachFont, FONT_FILES, offersCopy } from "../fonts";
import { enter, finish, troublesIn, typeInto } from "../typing";
import { useEditor, useEditorState } from "./context";
import styles from "./EditField.module.css";
import { boxOf, fieldScaleOf, points, spanTextStyle } from "./geometry";

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
  const face = font === undefined ? DEFAULT_FACE : previewFaceOf(font);
  const placed = useRef(false);
  const glyphs = font?.glyphs ?? {};

  // One trouble a line: a sentence of the server's, then the fit's own.
  const trouble = useEditorState((state) => troublesIn(state, state.draft?.text ?? "").said.join("\n"));
  const offered = useEditorState(offersCopy);
  const attaching = useEditorState((state) => state.attaching !== null);
  const field = useRef<HTMLInputElement>(null);
  const [scale, setScale] = useState(1);
  // As the page is shown wider or narrower, what the field scales by to show the span's size.
  const watchPage = useCallback(
    (input: HTMLInputElement | null) => {
      field.current = input;
      const layer = input?.offsetParent;
      if (input == null || !(layer instanceof HTMLElement)) return;
      const shown = new ResizeObserver(() => {
        const fontPx = parseFloat(getComputedStyle(input).fontSize);
        setScale(fieldScaleOf(span.size, { pageWidthPt: info.width, shownPx: layer.clientWidth, fontPx }));
      });
      shown.observe(layer);
      return () => shown.disconnect();
    },
    [span.size, info.width],
  );
  // A font file dropped on the field is the user's copy of the span's font.
  const { dropProps } = useDrop({
    ref: field,
    onDrop: (event) => {
      const dropped = event.items.filter(isFileDropItem).map((item) => item.getFile());
      void attachDropped(editor, span.font, dropped);
    },
  });
  // Text dragged here is the field's own to take, as typing is: useDrop would swallow it.
  const filesOnly = (handler?: DragEventHandler<HTMLInputElement>) => (event: DragEvent<HTMLInputElement>) => {
    if (event.dataTransfer.types.includes("Files")) handler?.(event);
  };

  // Once, as the field takes focus: the caret goes where the press was, the word under it,
  // or everything from the keyboard. Later focus keeps the caret where the user put it.
  function place(event: FocusEvent<HTMLInputElement>) {
    if (placed.current) return;
    placed.current = true;
    const field = event.currentTarget;
    const at = editor.store.get().draft?.atPt;
    if (at == null) field.select();
    else field.setSelectionRange(...wordAround(start, letterAt(start, at, { glyphs, size: span.size })));
    if (trouble !== "") say(editor, trouble);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter" && event.key !== "Escape") return;
    event.preventDefault();
    if (event.key === "Enter") enter(editor);
    else finish(editor, false, { returnFocus: true });
  }

  const box = boxOf(span.bbox, info);
  // Its size by CSS variables, which a touch screen's stylesheet sets it from (EditField.module.css).
  const { fontSize, height, ...where } = spanTextStyle(span, info, face);
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
          {...dropProps}
          onDragEnter={filesOnly(dropProps.onDragEnter)}
          onDragOver={filesOnly(dropProps.onDragOver)}
          onDragLeave={filesOnly(dropProps.onDragLeave)}
          onDrop={filesOnly(dropProps.onDrop)}
          ref={watchPage}
          autoFocus
          onFocus={place}
          className={trouble !== "" ? styles.troubleInput : styles.input}
          spellCheck={false}
          autoComplete="off"
          onKeyDown={onKeyDown}
          // Only leaving the field ends the typing: a file picker takes the window's focus, not the field's.
          onBlur={() => {
            if (document.hasFocus()) finish(editor, true);
          }}
          style={{
            ...where,
            "--field-size": fontSize,
            "--field-width": points(wide, info),
            "--field-height": height,
            "--field-scale": scale,
          }}
        />
      </TextField>
      <div className={styles.chip} style={{ left: box.left, top: points(span.bbox.y1 + 4, info) }}>
        {trouble !== "" ? (
          <span className={styles.bad} aria-hidden="true">
            <Warn>{trouble}</Warn>
          </span>
        ) : (
          <span className={styles.facts} aria-hidden="true">
            <span>{face}</span>
            <Num>{SIZE.format(span.size)} pt</Num>
          </span>
        )}
        {offered && (
          // A pointer's shortcut to the Fonts list, the keyboard's way: focus stays in the field.
          <span className={styles.offer}>
            Have this font?
            <FileTrigger
              acceptedFileTypes={FONT_FILES}
              onSelect={(files) => {
                const file = files?.[0];
                if (file !== undefined) void attachFont(editor, span.font, file);
              }}
            >
              <Button preventFocusOnPress excludeFromTabOrder isDisabled={attaching}>
                Use your copy
              </Button>
            </FileTrigger>
          </span>
        )}
      </div>
    </>
  );
}
