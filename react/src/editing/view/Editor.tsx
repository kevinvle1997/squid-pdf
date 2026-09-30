import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { ProblemError, exportPdf, stillThere, upload } from "../../api/client";
import type { Document, SpanInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { Notice } from "../../ui/Notice";
import { SkipLink } from "../../ui/SkipLink";
import { Status } from "../../ui/Status";
import { Warn } from "../../ui/Warn";
import { Wordmark } from "../../ui/Wordmark";
import { MAX_SCALE, MIN_SCALE, PX_PER_PT } from "../constants";
import { EditorContext, type Editing, type EditorState, focusSpan } from "./context";
import styles from "./Editor.module.css";
import { EMPTY_LOG, type LogAction, editsOf, latestTexts, logReducer } from "../log";
import { Page } from "./Page";
import { useStrips } from "./useStrips";
import { counted } from "../words";

// Page images are drawn for this screen's pixels: sharp, and no larger than the API draws.
const SCALE = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.ceil(window.devicePixelRatio * PX_PER_PT)));
const APPLE = /Mac|iPhone|iPad/.test(navigator.userAgent);
const COMMAND = APPLE ? "⌘" : "Ctrl ";

interface Said {
  tone: "plain" | "warn";
  text: string;
}

function byPage(spans: readonly SpanInfo[]): Map<number, SpanInfo[]> {
  const pages = new Map<number, SpanInfo[]>();
  for (const span of spans) pages.set(span.page, [...(pages.get(span.page) ?? []), span]);
  return pages;
}

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
}

function download(pdf: Blob, name: string): void {
  const url = URL.createObjectURL(pdf);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  // Not at once: some browsers are still reading the file when click() returns.
  window.setTimeout(() => URL.revokeObjectURL(url));
}

/** The open document: every page, every span marked, editable in place. */
export function Editor({ file, opened }: { file: File; opened: Document }) {
  const [doc, setDoc] = useState(opened);
  // The history and the text being typed. Each ref is what handlers read, current the moment
  // it changes (export reads both straight after finishing the typing); the state redraws.
  const history = useRef(EMPTY_LOG);
  const [log, setLog] = useState(EMPTY_LOG);
  const edits = useMemo(() => editsOf(log), [log]);
  const latest = useMemo(() => latestTexts(edits), [edits]);
  const draft = useRef<Editing | null>(null);
  const [editing, setEditingState] = useState<Editing | null>(null);
  const setEditing = (next: Editing | null) => {
    draft.current = next;
    setEditingState(next);
  };
  const [returnedTo, returnTo] = useState<string | null>(null);
  const [notice, setNotice] = useState<Said | null>(() => {
    const [first] = opened.notices;
    return first === undefined ? null : { tone: "warn", text: first.detail };
  });
  const [said, setSaid] = useState("");
  const fonts = useMemo(() => new Map(doc.fonts.map((font) => [font.name, font])), [doc]);
  const spans = useMemo(() => new Map(doc.spans.map((span) => [span.id, span])), [doc]);
  const pages = useMemo(() => byPage(doc.spans), [doc]);

  // The hour ran out: open the same file again. Span ids are the same, so every edit still applies.
  const reopening = useRef<Promise<Document | null> | null>(null);
  function reopen(): Promise<Document | null> {
    reopening.current ??= upload(file, () => undefined)
      .then((again) => {
        setDoc(again);
        setNotice({ tone: "plain", text: again.copy.reopened });
        return again;
      })
      .catch((error: unknown) => {
        if (!(error instanceof ProblemError)) throw error;
        setNotice({ tone: "warn", text: error.problem.detail });
        return null;
      })
      .finally(() => {
        reopening.current = null;
      });
    return reopening.current;
  }

  const onProblem = (detail: string) => setNotice({ tone: "warn", text: detail });
  const { strips, shown, fits, redraw } = useStrips({ scale: SCALE, reopen, onProblem });

  // Every change to the history comes through here, and redraws what it changed.
  function change(action: LogAction) {
    const before = history.current;
    const after = logReducer(before, action);
    if (after === before) return;
    history.current = after;
    setLog(after);
    // A message about the last export or reopening is stale once the user edits again.
    setNotice((now) => (now?.tone === "plain" ? null : now));
    redraw(doc, editsOf(before), editsOf(after));
  }

  // Typing ends once, however it ends: Enter, Escape, leaving the field, or an export.
  function finish(keep: boolean) {
    const typed = draft.current;
    if (typed === null) return;
    setEditing(null);
    const was = latestTexts(editsOf(history.current)).get(typed.spanId) ?? spans.get(typed.spanId)?.text;
    // Emptying a span isn't a replacement: taking text out is redaction's job.
    if (!keep || typed.text === was || typed.text.trim() === "") return;
    change({ kind: "add", step: [{ kind: "replace", span_id: typed.spanId, text: typed.text }] });
    setSaid(`Changed to ${typed.text}`);
  }

  // A page image can fail for any reason; only a document that's really gone is opened again.
  async function onImageFailed() {
    if (!(await stillThere(doc.id))) await reopen();
  }

  const changed = [...latest].filter(([id, text]) => spans.get(id)?.text !== text);
  const similar = changed.filter(([id]) => {
    const span = spans.get(id);
    const inSimilar = span !== undefined && fonts.get(span.font)?.substitute != null;
    return inSimilar || (fits[id]?.missing.length ?? 0) > 0;
  });

  const exporting = useRef(false);
  const [busy, setBusy] = useState(false);
  async function exportNow() {
    if (exporting.current) return;
    exporting.current = true;
    setBusy(true);
    // An edit still being typed goes in first.
    finish(true);
    const edits = editsOf(history.current);
    try {
      let exported;
      try {
        exported = await exportPdf(doc.id, edits);
      } catch (error) {
        // Only an expired document is worth one quiet retry; anything else is said as it is.
        if (!(error instanceof ProblemError) || error.problem.status !== 404) throw error;
        const again = await reopen();
        if (again === null) return;
        exported = await exportPdf(again.id, edits);
      }
      download(exported.pdf, file.name);
      const leftOut = exported.skipped.length > 0;
      const text = leftOut
        ? doc.copy.export_left_out
        : (exported.notices[0]?.detail ?? `Downloaded ${file.name}.`);
      setNotice({ tone: leftOut ? "warn" : "plain", text });
      setSaid(text);
    } catch (error) {
      if (!(error instanceof ProblemError)) throw error;
      setNotice({ tone: "warn", text: error.problem.detail });
      setSaid(error.problem.detail);
    } finally {
      exporting.current = false;
      setBusy(false);
    }
  }

  // The one effect: shortcuts work wherever focus is, so they listen on the window.
  const onKey = useEffectEvent((event: KeyboardEvent) => {
    if (!(event.metaKey || event.ctrlKey)) return;
    const key = event.key.toLowerCase();
    if (key === "s") {
      event.preventDefault();
      void exportNow();
      return;
    }
    // In a text field, undo and redo are the field's own.
    if (isTyping(event.target)) return;
    if (key === "z" || key === "y") {
      event.preventDefault();
      change({ kind: key === "y" || event.shiftKey ? "redo" : "undo" });
    }
  });
  useEffect(() => {
    const listen = (event: KeyboardEvent) => onKey(event);
    window.addEventListener("keydown", listen);
    return () => window.removeEventListener("keydown", listen);
  }, []);

  const state: EditorState = {
    doc,
    scale: SCALE,
    fonts,
    latest,
    shown,
    fits,
    editing,
    returnedTo,
    returnTo,
    edit: (spanId, atPt) => setEditing({ spanId, atPt, text: latest.get(spanId) ?? spans.get(spanId)?.text ?? "" }),
    type: (text) => {
      if (draft.current !== null) setEditing({ ...draft.current, text });
    },
    finish,
    revert: (spanId) => {
      change({ kind: "revert", spanId });
      setSaid(`Put back ${spans.get(spanId)?.text ?? ""}`);
      focusSpan(spanId);
    },
    say: setSaid,
    imageFailed: () => void onImageFailed(),
  };

  return (
    <EditorContext.Provider value={state}>
      <SkipLink to="pages">Skip to the document</SkipLink>
      <header className={styles.bar}>
        <Wordmark />
        <span className={styles.file}>
          <span className={styles.name}>{file.name}</span>
          <span className={styles.meta}>{counted(doc.pages.length, { one: "page", other: "pages" })}</span>
        </span>
        <span className={styles.grow} />
        <span className={styles.status}>
          {changed.length > 0 && counted(changed.length, { one: "change", other: "changes" })}
          {similar.length > 0 && (
            <>
              {" · "}
              <Warn>{similar.length}</Warn> in a similar font
            </>
          )}
        </span>
        <Button onPress={() => void exportNow()} isDisabled={busy}>
          Export <kbd>{COMMAND}S</kbd>
        </Button>
      </header>
      {notice !== null && <Notice tone={notice.tone}>{notice.text}</Notice>}
      <main id="pages" className={styles.pages} tabIndex={-1}>
        {doc.pages.map((info, index) => (
          <Page key={index} index={index} info={info} spans={pages.get(index) ?? []} strips={strips.get(index)} />
        ))}
      </main>
      <Status>{said}</Status>
    </EditorContext.Provider>
  );
}
