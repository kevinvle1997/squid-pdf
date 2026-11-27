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
import { neverEdited } from "../page";
import { pagesShown } from "../project";
import { takesSelection } from "../redaction";
import { loadShippedFaces } from "../shippedFaces";
import { isWork, readStoredAgain, restorePersisted, resumeWork, wouldLoseWork } from "../workPersistence";
import { Bar } from "./Bar";
import { EditorContext, useEditor, useEditorState } from "./context";
import { DeletedPage } from "./DeletePage";
import styles from "./EditorShell.module.css";
import { FindBar } from "./FindBar";
import { HowToStart } from "./HowToStart";
import { isOnMarginNote, isTyping, newTextOn, pageOn, selectedIn, spanOn } from "./keys";
import { Page } from "./Page";
import { PageList } from "./PageList";
import { SplitHere } from "./SplitHere";
import { ZoomControl } from "./Zoom";

const SCALE = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.ceil(window.devicePixelRatio * PX_PER_PT)));

interface Props {
  opened: Opened;
  onOpen: (file: File) => void;
  persisted?: PersistedWork<unknown> | undefined;
}

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

  useEffect(() => {
    addFaces(facesOf(editor.store.get().doc));
    void loadShippedFaces(editor);
    resumeWork(editor);
    announceOpening(editor);
    const key = (event: KeyboardEvent) => {
      const { target } = event;
      const selected = selectedIn(target);
      const selecting = selected !== null && takesSelection(editor.store.get(), selected);
      const command = commandFor(event, isTyping(target), { selecting });
      if (command === undefined) return;
      event.preventDefault();
      const marginNote = isOnMarginNote(target);
      const pressedOn = { page: pageOn(target), newTextId: newTextOn(target), marginNote };
      command.run(editor, { ...pressedOn, spanId: spanOn(target), selected });
    };
    const online = () => {
      editor.queue.retry();
      retryImages(editor);
      readStoredAgain(editor);
    };
    // A file dragged anywhere drops here: the browser would open it in this tab, and the edits would go.
    const dragging = (event: DragEvent) => {
      if (carriesFiles(event.dataTransfer)) event.preventDefault();
    };
    const dropped = (event: DragEvent) => {
      if (event.defaultPrevented || !carriesFiles(event.dataTransfer)) return;
      event.preventDefault();
      openDropped([...(event.dataTransfer?.files ?? [])]);
    };
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
      <div className={styles.top}>
        <Bar />
        <FindBar />
        <PageList />
        <div className={styles.notices}>
          <NoticeLine />
        </div>
      </div>
      <Pages />
      <Announcer />
    </EditorContext.Provider>
  );
}

function Title() {
  const editor = useEditor();
  const changed = useEditorState((state) => state.reading.edits.length > 0 && unexported(state));
  return <title>{`${changed ? "* " : ""}${editor.file.name} · squid-pdf`}</title>;
}

function NoticeLine() {
  const notices = useEditorState((state) => state.notices);
  const drawn = useEditorState((state) => state.drawn);
  const deleted = useEditorState((state) => state.reading.deleted);
  const edits = useEditorState((state) => state.reading.edits);
  const lines = useMemo(() => noticeLines(notices, drawn, { deleted, edits }), [notices, drawn, deleted, edits]);
  return lines.map((line) => (
    <Notice key={line.text} tone={line.tone}>
      {line.text}
    </Notice>
  ));
}

function Pages() {
  const pages = useEditorState((state) => state.doc.pages);
  const order = useEditorState((state) => state.reading.order);
  const shown = useMemo(() => pagesShown(order, pages), [order, pages]);
  const asked = useEditorState((state) => state.zoom.asked);
  const howToStart = useEditorState(neverEdited);
  const zoomed = asked === null ? undefined : { "--zoom": asked };
  return (
    <main
      id="pages"
      className={styles.pages}
      tabIndex={-1}
      style={zoomed}
      data-zoomed={asked === null ? undefined : ""}
    >
      {howToStart && <HowToStart beside={false} />}
      {shown.map(({ page, info, shownNumber, splitAfter }) => (
        <Fragment key={page}>
          {shownNumber === null ? (
            <DeletedPage page={page} info={info} />
          ) : (
            <Page index={page} info={info} shownNumber={shownNumber} />
          )}
          {shownNumber !== null && splitAfter && <SplitHere after={page} shownNumber={shownNumber} info={info} />}
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
