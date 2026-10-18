import { Fragment, useEffect, useEffectEvent, useMemo, useState } from "react";
import type { KeptWork } from "../../documents/kept";
import type { Opened } from "../../documents/open";
import { Notice } from "../../ui/Notice";
import { SkipLink } from "../../ui/SkipLink";
import { Status } from "../../ui/Status";
import { commandFor } from "../commands";
import { MAX_SCALE, MIN_SCALE, PX_PER_PT } from "../constants";
import { carriesFiles, pdfDropped } from "../dropped";
import { closeEditor, createEditor, sayOpening } from "../editor";
import { addFaces, facesOf } from "../faces";
import { retryImages } from "../images";
import { atRisk, isWork, resumeWork, takeUp } from "../keeping";
import { noticeLines } from "../notices";
import { Bar } from "./Bar";
import { EditorContext, useEditorState } from "./context";
import styles from "./EditorShell.module.css";
import { FindBar } from "./FindBar";
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

/** The props of the editor: the document opened, what opens a PDF dropped on it, and work kept here to take up. */
interface Props {
  opened: Opened;
  onOpen: (file: File) => void;
  kept?: KeptWork<unknown> | undefined;
}

/**
 * The open document: every page, every span marked, editable in place, under the bar. `onOpen`
 * opens a PDF dropped on it, in its place. Opened again from this browser, it takes up the work kept.
 */
export function EditorShell({ opened, onOpen, kept }: Props) {
  const [editor] = useState(() => {
    const made = createEditor(opened.file, opened.doc, SCALE);
    if (kept !== undefined && isWork(kept.work)) takeUp(made, { work: kept.work, until: kept.until });
    return made;
  });
  const openDropped = useEffectEvent((files: readonly File[]) => {
    const pdf = pdfDropped(editor, files);
    if (pdf !== null) onOpen(pdf);
  });

  // The one effect, for what happens outside React: the preview's faces, the live region on screen,
  // shortcuts anywhere, the connection coming back, a file dropped anywhere, leaving with edits not
  // exported, and the editor going.
  useEffect(() => {
    addFaces(facesOf(editor.store.get().doc));
    resumeWork(editor);
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
      if (carriesFiles(event.dataTransfer)) event.preventDefault();
    };
    // The field a font goes on takes its own drop first.
    const dropped = (event: DragEvent) => {
      if (event.defaultPrevented || !carriesFiles(event.dataTransfer)) return;
      event.preventDefault();
      openDropped([...(event.dataTransfer?.files ?? [])]);
    };
    // The browser asks before leaving, in its own words, only when work would be lost: not kept here yet.
    const leaving = (event: BeforeUnloadEvent) => {
      if (atRisk(editor)) event.preventDefault();
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
