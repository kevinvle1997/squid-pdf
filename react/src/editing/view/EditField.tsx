import {
  type DragEvent,
  type DragEventHandler,
  type FocusEvent,
  type KeyboardEvent,
  useCallback,
  useReducer,
  useRef,
  useState,
} from "react";
import { isFileDropItem, useDrop } from "react-aria";
import { Input, TextField } from "react-aria-components";
import type { PageInfo, SpanInfo } from "../../api/types";
import { selectionAt } from "../caret";
import { carriesFiles } from "../dropped";
import { DEFAULT_FACE, previewFaceOf } from "../faces";
import { settingOf } from "../fit";
import { attachDropped } from "../fonts";
import { textIn } from "../project";
import { enter, fieldFocused, finish, stepWayOut, troublesIn, typeInto } from "../typing";
import { Chip } from "./Chip";
import { useEditor, useEditorState } from "./context";
import styles from "./EditField.module.css";
import { fieldScaleOf, fieldWidthPt, points, spanTextStyle } from "./geometry";

/**
 * Typing in place: the span's text in the face that will draw it, checked as it's typed.
 * It stands where the span's mark stood, so Tab and Shift+Tab go on to the next span unaided.
 * The editor holds what's typed, so an export mid-word takes it too.
 */
export function EditField({ span, info }: { span: SpanInfo; info: PageInfo }) {
  const editor = useEditor();
  const text = useEditorState((state) => state.draft?.text ?? "");
  const font = useEditorState((state) => state.layout.fonts.get(span.font));
  const start = useEditorState((state) => textIn(state.reading, span));
  const face = font === undefined ? DEFAULT_FACE : previewFaceOf(span, { font, text });
  const placed = useRef(false);
  const glyphs = font?.glyphs ?? {};
  // A line past its room offers the ways out, and the one chosen is drawn here at once, as the server will.
  const rules = useEditorState((state) => state.doc.fit);
  const asked = useEditorState((state) => state.draft?.strategy);
  const setting = settingOf(span, { font, text, asked, rules });
  // One trouble a line: a sentence of the server's, then the fit's own.
  const trouble = useEditorState((state) => troublesIn(state, state.draft?.text ?? "").said.join("\n"));
  const field = useRef<HTMLInputElement>(null);
  const [scale, setScale] = useState(1);
  const [, measureAgain] = useReducer((times: number) => times + 1, 0);
  // As the page is shown wider or narrower, what the field scales by to show the span's size.
  const watchPage = useCallback(
    (input: HTMLInputElement | null) => {
      field.current = input;
      const layer = input?.offsetParent;
      if (input == null || !(layer instanceof HTMLElement)) return;
      const resized = new ResizeObserver(() => {
        const fontPx = parseFloat(getComputedStyle(input).fontSize);
        setScale(fieldScaleOf(span.size, { pageWidthPt: info.width, shownPx: layer.clientWidth, fontPx }));
      });
      resized.observe(layer);
      // A face that loads once the field is open draws its letters wider or narrower than first measured.
      document.fonts.addEventListener("loadingdone", measureAgain);
      return () => {
        resized.disconnect();
        document.fonts.removeEventListener("loadingdone", measureAgain);
      };
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
    if (carriesFiles(event.dataTransfer)) handler?.(event);
  };

  // Once, as the field takes focus: what the press that opened it selects, or everything from
  // the keyboard. Later focus keeps the caret where the user put it.
  function place(event: FocusEvent<HTMLInputElement>) {
    if (placed.current) return;
    placed.current = true;
    const input = event.currentTarget;
    const press = editor.store.get().draft?.press;
    if (press == null) input.select();
    else input.setSelectionRange(...selectionAt(start, press, { glyphs, size: span.size }));
    fieldFocused(editor);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    // Up and Down choose how a long line fits; while it fits, they're the field's own.
    const step = { ArrowUp: -1, ArrowDown: 1 } as const;
    if ((event.key === "ArrowUp" || event.key === "ArrowDown") && stepWayOut(editor, step[event.key])) {
      event.preventDefault();
      return;
    }
    if (event.key !== "Enter" && event.key !== "Escape") return;
    event.preventDefault();
    if (event.key === "Enter") enter(editor);
    else finish(editor, false, { returnFocus: true });
  }

  // Its size by CSS variables, which a touch screen's stylesheet sets it from (EditField.module.css).
  const { fontSize, height, ...where } = spanTextStyle(span, info, face);
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
            "--field-width": points(fieldWidthPt(span, { text, face, glyphs }), info),
            "--field-height": height,
            "--field-scale": scale,
            "--field-shrink": setting.size,
            "--field-narrow": setting.scaleX,
          }}
        />
      </TextField>
      <Chip span={span} info={info} trouble={trouble} setting={setting} />
    </>
  );
}
