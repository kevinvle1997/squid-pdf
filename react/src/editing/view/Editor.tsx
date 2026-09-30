import { useEffect, useState } from "react";
import type { Document } from "../../api/types";
import { Button } from "../../ui/Button";
import { Notice } from "../../ui/Notice";
import { SkipLink } from "../../ui/SkipLink";
import { Status } from "../../ui/Status";
import { Warn } from "../../ui/Warn";
import { Wordmark } from "../../ui/Wordmark";
import { MAX_SCALE, MIN_SCALE, PX_PER_PT } from "../constants";
import { change, changedCount, createEditor, type Editor as OpenDocument, similarCount } from "../editor";
import { exportNow } from "../export";
import { counted } from "../words";
import { EditorContext, useEditor, useEditorState } from "./context";
import styles from "./Editor.module.css";
import { Page } from "./Page";

// Page images are drawn for this screen's pixels: sharp, and no larger than the API draws.
const SCALE = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.ceil(window.devicePixelRatio * PX_PER_PT)));
const APPLE = /Mac|iPhone|iPad/.test(navigator.userAgent);
const COMMAND = APPLE ? "⌘" : "Ctrl ";

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
}

/** Shortcuts, wherever focus is. */
function onKey(editor: OpenDocument, event: KeyboardEvent): void {
  if (!(event.metaKey || event.ctrlKey)) return;
  const key = event.key.toLowerCase();
  if (key === "s") {
    event.preventDefault();
    void exportNow(editor);
    return;
  }
  // In a text field, undo and redo are the field's own.
  if (isTyping(event.target)) return;
  if (key === "z" || key === "y") {
    event.preventDefault();
    change(editor, { kind: key === "y" || event.shiftKey ? "redo" : "undo" });
  }
}

/** The open document: every page, every span marked, editable in place. */
export function Editor({ file, opened }: { file: File; opened: Document }) {
  const [editor] = useState(() => createEditor(file, opened, SCALE));

  // The one effect, for what happens outside React: shortcuts anywhere, and the connection coming back.
  useEffect(() => {
    const key = (event: KeyboardEvent) => onKey(editor, event);
    const online = () => editor.queue.retry();
    window.addEventListener("keydown", key);
    window.addEventListener("online", online);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("online", online);
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
  const similar = useEditorState(similarCount);
  const exporting = useEditorState((state) => state.exporting);
  return (
    <header className={styles.bar}>
      <Wordmark />
      <span className={styles.file}>
        <span className={styles.name}>{editor.file.name}</span>
        <span className={styles.meta}>{counted(pages, { one: "page", other: "pages" })}</span>
      </span>
      <span className={styles.grow} />
      <span className={styles.status}>
        {changed > 0 && counted(changed, { one: "change", other: "changes" })}
        {similar > 0 && (
          <>
            {" · "}
            <Warn>{similar}</Warn> in a similar font
          </>
        )}
      </span>
      <Button onPress={() => void exportNow(editor)} isDisabled={exporting}>
        Export <kbd>{COMMAND}S</kbd>
      </Button>
    </header>
  );
}

function NoticeLine() {
  const notice = useEditorState((state) => state.notice);
  return notice === null ? null : <Notice tone={notice.tone}>{notice.text}</Notice>;
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
  return <Status>{useEditorState((state) => state.said)}</Status>;
}
