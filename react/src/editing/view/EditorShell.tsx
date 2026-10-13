import { Fragment, useCallback, useEffect, useEffectEvent, useMemo, useState } from "react";
import type { Document } from "../../api/types";
import { QUIET_MS } from "../../documents/constants";
import { Button } from "../../ui/Button";
import { Notice } from "../../ui/Notice";
import { SkipLink } from "../../ui/SkipLink";
import { Status } from "../../ui/Status";
import { Warn } from "../../ui/Warn";
import { Wordmark } from "../../ui/Wordmark";
import { commandFor } from "../commands";
import { MAX_SCALE, MIN_SCALE, PX_PER_PT } from "../constants";
import { pdfDropped } from "../dropped";
import { changedCount, closeEditor, createEditor, sayOpening, unexported, unlikeAtRest, warnedCount } from "../editor";
import { exportAhead, exportIntended, exportNow, exportUnintended } from "../export";
import { addFaces, facesOf } from "../faces";
import { openFind } from "../find";
import { retryImages } from "../images";
import { noticeLines } from "../notices";
import { shallowEqual } from "../store";
import { counted, formOf } from "../words";
import { EditorContext, useEditor, useEditorState } from "./context";
import styles from "./EditorShell.module.css";
import { FindBar } from "./FindBar";
import { Fonts } from "./Fonts";
import { COMMAND } from "./keys";
import { Page } from "./Page";
import { SplitHere } from "./SplitHere";

// Page images are drawn for this screen's pixels: sharp, and no larger than the API draws.
const SCALE = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.ceil(window.devicePixelRatio * PX_PER_PT)));

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
}

/** The span a key was pressed on: its mark says which, as `data-span`. */
function spanOn(target: EventTarget | null): string | null {
  return target instanceof HTMLElement ? (target.dataset.span ?? null) : null;
}

/** Whether a drag carries files, as a drop from the desktop does, not text or a link. */
function carriesFiles(event: DragEvent): boolean {
  return event.dataTransfer?.types.includes("Files") ?? false;
}

/**
 * The open document: every page, every span marked, editable in place, under the bar. `onOpen`
 * opens a PDF dropped on it, in its place.
 */
export function EditorShell({ file, opened, onOpen }: { file: File; opened: Document; onOpen: (file: File) => void }) {
  const [editor] = useState(() => createEditor(file, opened, SCALE));
  const openDropped = useEffectEvent((files: readonly File[]) => {
    const pdf = pdfDropped(editor, files);
    if (pdf !== null) onOpen(pdf);
  });

  // The one effect, for what happens outside React: the preview's faces, the live region on screen,
  // shortcuts anywhere, the connection coming back, a file dropped anywhere, leaving with edits not
  // exported, and the editor going.
  useEffect(() => {
    addFaces(facesOf(editor.store.get().doc));
    sayOpening(editor);
    const key = (event: KeyboardEvent) => {
      const command = commandFor(event, isTyping(event.target));
      if (command === undefined) return;
      event.preventDefault();
      command.run(editor, spanOn(event.target));
    };
    const online = () => {
      editor.queue.retry();
      retryImages(editor);
    };
    // A file dragged anywhere drops here: the browser would open it in this tab, and the edits would go.
    const dragging = (event: DragEvent) => {
      if (carriesFiles(event)) event.preventDefault();
    };
    // The field a font goes on takes its own drop first.
    const dropped = (event: DragEvent) => {
      if (event.defaultPrevented || !carriesFiles(event)) return;
      event.preventDefault();
      openDropped([...(event.dataTransfer?.files ?? [])]);
    };
    // The browser asks before leaving, in its own words. Until this browser keeps the edits too.
    const leaving = (event: BeforeUnloadEvent) => {
      if (unexported(editor.store.get())) event.preventDefault();
    };
    window.addEventListener("keydown", key);
    window.addEventListener("online", online);
    window.addEventListener("dragover", dragging);
    window.addEventListener("drop", dropped);
    window.addEventListener("beforeunload", leaving);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("online", online);
      window.removeEventListener("dragover", dragging);
      window.removeEventListener("drop", dropped);
      window.removeEventListener("beforeunload", leaving);
      closeEditor(editor);
    };
  }, [editor]);

  return (
    <EditorContext.Provider value={editor}>
      <SkipLink to="pages">Skip to the document</SkipLink>
      {/* Held at the top together, however many rows the bar wraps to. */}
      <div className={styles.top}>
        <Bar />
        <FindBar />
      </div>
      <NoticeLine />
      <Pages />
      <Said />
    </EditorContext.Provider>
  );
}

function Bar() {
  const editor = useEditor();
  const pages = useEditorState((state) => state.doc.pages.length);
  const changed = useEditorState(changedCount);
  const warned = useEditorState(warnedCount, shallowEqual);
  const atRest = useEditorState((state) => unlikeAtRest(state.doc));
  const exporting = useEditorState((state) => state.exporting);
  // The document opened again, or couldn't: said in the bar's status, beside the counts.
  const reopen = useEditorState((state) => state.notices.reopen);
  return (
    <header className={styles.bar}>
      <Wordmark />
      <span className={styles.file}>
        <span className={styles.name}>{editor.file.name}</span>
        <span className={styles.meta}>{counted(pages, { one: "page", other: "pages" })}</span>
      </span>
      <span className={styles.grow} />
      {reopen !== null && (
        <span className={styles.reopen}>{reopen.tone === "warn" ? <Warn>{reopen.text}</Warn> : reopen.text}</span>
      )}
      {changed === 0 && (
        // Beside the count before any change, it gives way to it on a narrow screen.
        <span className={atRest.count > 0 ? `${styles.hint} ${styles.besideCount}` : styles.hint}>
          <span className={styles.click}>Double-click text to change it</span>
          <span className={styles.tap}>Double-tap text to change it</span>
        </span>
      )}
      <span className={styles.status}>
        {changed === 0 ? (
          <AtRest {...atRest} />
        ) : (
          <>
            {counted(changed, { one: "change", other: "changes" })}
            {warned.count > 0 && (
              <>
                {" · "}
                <Warn>{warned.count}</Warn> {warned.label}
              </>
            )}
          </>
        )}
      </span>
      {exporting && (
        // Hidden for the quiet spell by CSS, as opening a file is, so a quick export shows nothing.
        <span className={styles.exporting} style={{ animationDelay: `${QUIET_MS}ms` }}>
          Exporting
        </span>
      )}
      <FindButton />
      <Fonts />
      <Button
        onPress={() => void exportNow(editor)}
        // On the way to pressing it, the export starts, for the press to take.
        onHoverStart={() => exportIntended(editor)}
        onHoverEnd={() => exportUnintended(editor)}
        onFocus={() => exportIntended(editor)}
        onBlur={() => exportUnintended(editor)}
        onPressStart={() => exportAhead(editor)}
        isDisabled={exporting}
      >
        Export <kbd>{COMMAND}S</kbd>
      </Button>
    </header>
  );
}

/** Before any change, where an edit won't match, warned (Rule 1, for the whole document): nothing when it all will. */
function AtRest({ count, approximate }: { count: number; approximate: boolean }) {
  if (count === 0) return null;
  const places = formOf(count, { one: "place", other: "places" });
  return (
    <>
      Edits in <Warn>{count}</Warn> {places} {approximate ? "will look different" : "will use a similar font"}
    </>
  );
}

/** Opens find and replace; focus comes back here when the find bar closes from inside. */
function FindButton() {
  const editor = useEditor();
  const closed = useEditorState((state) => state.find.closed);
  const takeFocus = useCallback(
    (button: HTMLButtonElement | null) => {
      if (closed > 0) button?.focus();
    },
    [closed],
  );
  return (
    <Button ref={takeFocus} onPress={() => openFind(editor)}>
      Find <kbd>{COMMAND}F</kbd>
    </Button>
  );
}

function NoticeLine() {
  const notices = useEditorState((state) => state.notices);
  const drawn = useEditorState((state) => state.drawn);
  const lines = useMemo(() => noticeLines(notices, drawn), [notices, drawn]);
  return lines.map((line) => (
    <Notice key={line.text} tone={line.tone}>
      {line.text}
    </Notice>
  ));
}

function Pages() {
  const pages = useEditorState((state) => state.doc.pages);
  return (
    <main id="pages" className={styles.pages} tabIndex={-1}>
      {pages.map((info, index) => (
        <Fragment key={index}>
          <Page index={index} info={info} />
          {index < pages.length - 1 && <SplitHere after={index} info={info} />}
        </Fragment>
      ))}
    </main>
  );
}

function Said() {
  const said = useEditorState((state) => state.said);
  return <Status count={said.count}>{said.text}</Status>;
}
