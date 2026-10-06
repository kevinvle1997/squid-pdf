import { useEffect, useMemo, useState } from "react";
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
import { changedCount, closeEditor, createEditor, substitutedCount, unexported } from "../editor";
import { exportNow } from "../export";
import { addFaces, facesOf } from "../faces";
import { noticeLines } from "../notices";
import { counted } from "../words";
import { EditorContext, useEditor, useEditorState } from "./context";
import styles from "./EditorShell.module.css";
import { Fonts } from "./Fonts";
import { Page } from "./Page";

// Page images are drawn for this screen's pixels: sharp, and no larger than the API draws.
const SCALE = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.ceil(window.devicePixelRatio * PX_PER_PT)));
const APPLE = /Mac|iPhone|iPad/.test(navigator.userAgent);
const COMMAND = APPLE ? "⌘" : "Ctrl ";

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
}

/** The open document: every page, every span marked, editable in place, under the bar. */
export function EditorShell({ file, opened }: { file: File; opened: Document }) {
  const [editor] = useState(() => createEditor(file, opened, SCALE));

  // The one effect, for what happens outside React: the preview's faces, shortcuts anywhere, the
  // connection coming back, leaving the page with edits not exported, and the editor going.
  useEffect(() => {
    addFaces(facesOf(editor.store.get().doc.fonts));
    const key = (event: KeyboardEvent) => {
      const command = commandFor(event, isTyping(event.target));
      if (command === undefined) return;
      event.preventDefault();
      command.run(editor);
    };
    const online = () => editor.queue.retry();
    // The browser asks before leaving, in its own words. Until this browser keeps the edits too.
    const leaving = (event: BeforeUnloadEvent) => {
      if (unexported(editor.store.get())) event.preventDefault();
    };
    window.addEventListener("keydown", key);
    window.addEventListener("online", online);
    window.addEventListener("beforeunload", leaving);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("online", online);
      window.removeEventListener("beforeunload", leaving);
      closeEditor(editor);
    };
  }, [editor]);

  return (
    <EditorContext.Provider value={editor}>
      <SkipLink to="pages">Skip to the document</SkipLink>
      <Bar />
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
  const substituted = useEditorState(substitutedCount);
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
      <span className={styles.status}>
        {changed > 0 && counted(changed, { one: "change", other: "changes" })}
        {substituted > 0 && (
          <>
            {" · "}
            <Warn>{substituted}</Warn> in a similar font
          </>
        )}
      </span>
      {exporting && (
        // Hidden for the quiet spell by CSS, as opening a file is, so a quick export shows nothing.
        <span className={styles.exporting} style={{ animationDelay: `${QUIET_MS}ms` }}>
          Exporting
        </span>
      )}
      <Fonts />
      <Button onPress={() => void exportNow(editor)} isDisabled={exporting}>
        Export <kbd>{COMMAND}S</kbd>
      </Button>
    </header>
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
        <Page key={index} index={index} info={info} />
      ))}
    </main>
  );
}

function Said() {
  const said = useEditorState((state) => state.said);
  return <Status count={said.count}>{said.text}</Status>;
}
