import { Fragment, useEffect, useEffectEvent, useMemo, useState } from "react";
import type { PersistedWork } from "../../documents/localPersistence";
import type { Opened } from "../../documents/open";
import { Notice } from "../../ui/Notice";
import { SkipLink } from "../../ui/SkipLink";
import { Status } from "../../ui/Status";
import { commandFor } from "../commands";
import { MAX_SCALE, MIN_SCALE, PX_PER_PT } from "../constants";
import { carriesFiles, pdfDropped } from "../dropped";
import { announceOpening, closeEditor, createEditor } from "../editor";
import { unexported } from "../export";
import { addFaces, facesOf } from "../faces";
import { retryImages } from "../images";
import { noticeLines } from "../notices";
import { isWork, restorePersisted, resumeWork, wouldLoseWork } from "../workPersistence";
import { Bar } from "./Bar";
import { EditorContext, useEditor, useEditorState } from "./context";
import styles from "./EditorShell.module.css";
import { FindBar } from "./FindBar";
import { isTyping, spanOn } from "./keys";
import { Page } from "./Page";
import { SplitHere } from "./SplitHere";
import { ZoomControl } from "./Zoom";

// Page images are drawn for this screen's pixels: sharp, and no larger than the API draws.
const SCALE = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.ceil(window.devicePixelRatio * PX_PER_PT)));

/** The props of the editor: the document opened, what opens a PDF dropped on it, and work persisted here to restore. */
interface Props {
  opened: Opened;
  onOpen: (file: File) => void;
  persisted?: PersistedWork<unknown> | undefined;
}

/**
 * The open document: every page, every span marked, editable in place, under the bar. `onOpen`
 * opens a PDF dropped on it, in its place. Opened again from this browser, it restores the work persisted.
 */
export function EditorShell({ opened, onOpen, persisted }: Props) {
  const [editor] = useState(() => {
    const made = createEditor(opened.file, opened.doc, SCALE);
    if (persisted !== undefined && isWork(persisted.work))
      restorePersisted(made, { work: persisted.work, until: persisted.until });
    return made;
  });
  const openDropped = useEffectEvent((files: readonly File[]) => {
    const pdf = pdfDropped(editor, files);
    if (pdf !== null) onOpen(pdf);
  });

  // The one effect, for what happens outside React; each listener says what it's for.
  useEffect(() => {
    addFaces(facesOf(editor.store.get().doc));
    resumeWork(editor);
    announceOpening(editor);
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
    // The browser asks before leaving, in its own words, only when work would be lost: not persisted here yet.
    const leaving = (event: BeforeUnloadEvent) => {
      if (wouldLoseWork(editor)) event.preventDefault();
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
      <Title />
      {/* Held at the top together, however many rows the bar wraps to; the lines under it hang below, over the
          pages, so one coming or going never moves the document. */}
      <div className={styles.top}>
        <Bar />
        <FindBar />
        <div className={styles.notices}>
          <NoticeLine />
        </div>
      </div>
      <Pages />
      <Announcer />
    </EditorContext.Provider>
  );
}

/** The tab's title: the document's name, starred while it has changes the last export didn't take. */
function Title() {
  const editor = useEditor();
  const changed = useEditorState((state) => state.reading.edits.length > 0 && unexported(state));
  return <title>{`${changed ? "* " : ""}${editor.file.name} · squid-pdf`}</title>;
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
  const asked = useEditorState((state) => state.zoom.asked);
  // Zoomed to a share of the printed size: each page takes that much, and the pages scroll sideways past the window.
  const zoomed = asked === null ? undefined : { "--zoom": asked };
  return (
    <main
      id="pages"
      className={styles.pages}
      tabIndex={-1}
      style={zoomed}
      data-zoomed={asked === null ? undefined : ""}
    >
      {pages.map((info, index) => (
        <Fragment key={index}>
          <Page index={index} info={info} />
          {index < pages.length - 1 && <SplitHere after={index} info={info} />}
        </Fragment>
      ))}
      <ZoomControl />
    </main>
  );
}

function Announcer() {
  const announcement = useEditorState((state) => state.announcement);
  return <Status count={announcement.count}>{announcement.text}</Status>;
}
