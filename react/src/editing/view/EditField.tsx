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
import { FileTrigger, Input, TextField } from "react-aria-components";
import type { PageInfo, SpanInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { Choices } from "../../ui/Choices";
import { Num } from "../../ui/Num";
import { Warn } from "../../ui/Warn";
import { letterAt, wordAround } from "../caret";
import { say } from "../editor";
import { DEFAULT_FACE, previewFaceOf } from "../faces";
import { fitOf, setAs, wayDrawn, widthPt } from "../fit";
import { attachDropped, attachFont, FONT_FILES, offersCopy, shownNameOf } from "../fonts";
import { editNote, shortWarningOf } from "../marks";
import { quietFont } from "../quiet";
import { redact } from "../redaction";
import { chooseWayOut, enter, finish, stepWayOut, troublesIn, typeInto } from "../typing";
import { useEditor, useEditorState } from "./context";
import styles from "./EditField.module.css";
import { fieldScaleOf, pinnedUnder, points, shownWidthPt, spanTextStyle } from "./geometry";

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
  const face = font === undefined ? DEFAULT_FACE : previewFaceOf(span, { font, text });
  // What draws the edit: the span's similar font, or else its own, which the preview can only stand in for.
  const shown = useEditorState((state) => shownNameOf(state.layout, span.font));
  const drawnIn = span.substitute ?? shown;
  const placed = useRef(false);
  const glyphs = font?.glyphs ?? {};
  // A line past its room offers the ways out, and the one chosen is drawn here at once, as the server will.
  const rules = useEditorState((state) => state.doc.fit);
  const copy = useEditorState((state) => state.doc.copy);
  const asked = useEditorState((state) => state.draft?.strategy);
  const fit = font === undefined ? undefined : fitOf(span, { font, text, rules });
  const way = fit === undefined ? "as-is" : wayDrawn(asked, fit);
  const set = fit === undefined ? { size: 1, scaleX: 1 } : setAs(way, fit);
  // Leave it long alone is no choice: nothing to offer.
  const choosable = fit !== undefined && fit.options.length > 1 ? fit.options : [];
  const ways = choosable.map((id) => ({ id, label: copy.options[id]?.label ?? id }));

  // One trouble a line: a sentence of the server's, then the fit's own.
  const trouble = useEditorState((state) => troublesIn(state, state.draft?.text ?? "").said.join("\n"));
  const offered = useEditorState(offersCopy);
  // What an edit here warns of, in full, as typing starts: until the user quiets its font.
  const quiet = useEditorState((state) => state.quietFonts.has(shown));
  const note = editNote(span, { font, copy, quiet });
  const shortWarning = shortWarningOf(span);
  const attaching = useEditorState((state) => state.attaching !== null);
  const field = useRef<HTMLInputElement>(null);
  const [scale, setScale] = useState(1);
  const [, measureAgain] = useReducer((times: number) => times + 1, 0);
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
      // A face that loads once the field is open draws its letters wider or narrower than first measured.
      document.fonts.addEventListener("loadingdone", measureAgain);
      return () => {
        shown.disconnect();
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
    const heard = [note?.said, note?.why, trouble].filter(Boolean).join(" ");
    if (heard !== "") say(editor, heard);
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
  // The file's widths count a letter its font lacks as nothing, which the browser draws in a fallback; half an em spare.
  const typedWidth = Math.max(widthPt(text, glyphs, span.size), shownWidthPt(text, face, span.size));
  const wide = Math.max(span.bbox.x1 - span.bbox.x0, typedWidth + span.size / 2);
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
            "--field-shrink": set.size,
            "--field-narrow": set.scaleX,
          }}
        />
      </TextField>
      <div className={styles.chip} style={{ ...pinnedUnder(span.bbox, info), top: points(span.bbox.y1 + 4, info) }}>
        {note !== null && (
          // Said as the field takes focus; its button is a pointer's, the keyboard's way is the Fonts list.
          <span className={styles.note}>
            <span className={styles.noteWords} aria-hidden="true">
              <Warn mark>{note.said}</Warn>
              {note.why !== null && <span>{note.why}</span>}
            </span>
            <Button preventFocusOnPress excludeFromTabOrder onPress={() => quietFont(editor, shown, { quiet: true })}>
              Don't show again for this font
            </Button>
          </span>
        )}
        {trouble !== "" ? (
          <span className={styles.bad} aria-hidden="true">
            <Warn>{trouble}</Warn>
          </span>
        ) : (
          <span className={styles.facts} aria-hidden="true">
            <span>{drawnIn}</span>
            <Num>{SIZE.format(span.size * set.size)} pt</Num>
            {/* The verdict, before the edit (Rule 1): find's own words when it will match. */}
            {shortWarning === null ? (
              <span className={styles.identical}>looks identical</span>
            ) : (
              <Warn>{shortWarning}</Warn>
            )}
          </span>
        )}
        {ways.length > 0 && (
          // A pointer's way to choose; the keyboard's is Up and Down in the field, which keeps focus.
          <span className={styles.ways}>
            <Choices
              label="How it fits"
              options={ways}
              chosen={way}
              onChoose={(next) => chooseWayOut(editor, next)}
              keepFocus
            />
            <span className={styles.wayKeys} aria-hidden="true">
              <kbd>↑↓</kbd>
            </span>
          </span>
        )}
        <span className={styles.actions}>
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
          {/* Redaction where a pointer or a finger can reach it: a key on the mark is the keyboard's way. */}
          <span className={styles.redact}>
            <Button preventFocusOnPress excludeFromTabOrder onPress={() => redact(editor, span.id)}>
              Redact
            </Button>
          </span>
        </span>
      </div>
    </>
  );
}
