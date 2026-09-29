import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { Button } from "react-aria-components";
import { ProblemError, exportPdf, stillThere, upload } from "../api/client";
import type { Document, SpanInfo } from "../api/types";
import { Wordmark } from "../Wordmark";
import { MAX_SCALE, MIN_SCALE, PX_PER_PT } from "./constants";
import { EditorContext, type Editing, type EditorState, focusSpan } from "./context";
import styles from "./Editor.module.css";
import { EMPTY_LOG, editsOf, latestTexts, logReducer } from "./log";
import { Page } from "./Page";
import { useStrips } from "./useStrips";
import { counted } from "./words";

// Page images are drawn for this screen's pixels: sharp, and no larger than the API draws.
const SCALE = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.ceil(window.devicePixelRatio * PX_PER_PT)));
const APPLE = /Mac|iPhone|iPad/.test(navigator.userAgent);
const COMMAND = APPLE ? "⌘" : "Ctrl ";

interface Notice {
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
  const [log, dispatch] = useReducer(logReducer, EMPTY_LOG);
  const edits = useMemo(() => editsOf(log), [log]);
  // Export reads this, not `edits`: an edit committed just before it is in here already.
  const editsNow = useRef(edits);
  editsNow.current = edits;
  const latest = useMemo(() => latestTexts(edits), [edits]);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [returnedTo, returnTo] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(() => {
    const [first] = opened.notices;
    return first === undefined ? null : { tone: "warn", text: first.detail };
  });
  const [said, setSaid] = useState("");
  const fonts = useMemo(() => new Map(doc.fonts.map((font) => [font.name, font])), [doc]);
  const spans = useMemo(() => new Map(doc.spans.map((span) => [span.id, span])), [doc]);
  const pages = useMemo(() => byPage(doc.spans), [doc]);

  // The hour ran out: open the same file again. Span ids are the same, so every edit still applies.
  const reopening = useRef<Promise<Document | null> | null>(null);
  const reopen = useCallback((): Promise<Document | null> => {
    reopening.current ??= upload(file, () => undefined)
      .then((again) => {
        setDoc(again);
        setNotice({ tone: "plain", text: "This document's hour ran out, so it was opened again from this browser." });
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
  }, [file]);

  const onProblem = useCallback((detail: string) => setNotice({ tone: "warn", text: detail }), []);
  const onExpired = useCallback(() => void reopen(), [reopen]);
  // A page image can fail for any reason; only a document that's really gone is opened again.
  const onImageFailed = useCallback(async () => {
    if (!(await stillThere(doc.id))) await reopen();
  }, [doc.id, reopen]);

  // A message about the last export or reopening is stale once the user edits again.
  useEffect(() => setNotice((now) => (now?.tone === "plain" ? null : now)), [edits]);
  const { strips, previews, fits, settled } = useStrips({ doc, edits, scale: SCALE, onExpired, onProblem });

  const changed = [...latest].filter(([id, text]) => spans.get(id)?.text !== text);
  const similar = changed.filter(([id]) => {
    const span = spans.get(id);
    const inSimilar = span !== undefined && fonts.get(span.font)?.substitute != null;
    return inSimilar || (fits[id]?.missing.length ?? 0) > 0;
  });

  const exporting = useRef(false);
  const [busy, setBusy] = useState(false);
  const exportNow = useCallback(async () => {
    if (exporting.current) return;
    exporting.current = true;
    setBusy(true);
    // An edit still being typed goes in first: leaving the field commits it.
    flushSync(() => (document.activeElement as HTMLElement | null)?.blur());
    const edits = editsNow.current;
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
      const left = exported.skipped.length;
      const text =
        left > 0
          ? `Downloaded, but ${counted(left, { one: "change was", other: "changes were" })} left out: they point at text that isn't in this document.`
          : (exported.notices[0]?.detail ?? `Downloaded ${file.name}.`);
      setNotice({ tone: left > 0 ? "warn" : "plain", text });
      setSaid(text);
    } catch (error) {
      if (!(error instanceof ProblemError)) throw error;
      setNotice({ tone: "warn", text: error.problem.detail });
      setSaid(error.problem.detail);
    } finally {
      exporting.current = false;
      setBusy(false);
    }
  }, [doc.id, file.name, reopen]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
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
        dispatch({ kind: key === "y" || event.shiftKey ? "redo" : "undo" });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [exportNow]);

  const state: EditorState = {
    doc,
    scale: SCALE,
    fonts,
    latest,
    fits,
    previews,
    editing,
    returnedTo,
    returnTo,
    edit: (spanId, atPt) => setEditing({ spanId, atPt }),
    commit: (spanId, text) => {
      setEditing(null);
      const was = latest.get(spanId) ?? spans.get(spanId)?.text;
      // Emptying a span isn't a replacement: taking text out is redaction's job.
      if (text === was || text.trim() === "") return;
      dispatch({ kind: "add", step: [{ kind: "replace", span_id: spanId, text }] });
      setSaid(`Changed to ${text}`);
    },
    stopEditing: () => setEditing(null),
    revert: (spanId) => {
      dispatch({ kind: "revert", spanId });
      setSaid(`Put back ${spans.get(spanId)?.text ?? ""}`);
      focusSpan(spanId);
    },
    settled,
    say: setSaid,
    imageFailed: () => void onImageFailed(),
  };

  return (
    <EditorContext.Provider value={state}>
      <a className={styles.skip} href="#pages">
        Skip to the document
      </a>
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
              <b className={styles.warn}>{similar.length}</b> in a similar font
            </>
          )}
        </span>
        <Button className={styles.export} onPress={() => void exportNow()} isDisabled={busy}>
          Export <kbd>{COMMAND}S</kbd>
        </Button>
      </header>
      {notice !== null && <p className={notice.tone === "warn" ? styles.noticeWarn : styles.notice}>{notice.text}</p>}
      <main id="pages" className={styles.pages} tabIndex={-1}>
        {doc.pages.map((info, index) => (
          <Page key={index} index={index} info={info} spans={pages.get(index) ?? []} strips={strips.get(index)} />
        ))}
      </main>
      <p className="vh" role="status">
        {said}
      </p>
    </EditorContext.Provider>
  );
}
