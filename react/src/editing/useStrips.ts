// The server's render of each edited row, kept in step with the edit list.
// Typing never waits on it: the browser previews at once, and the render replaces the
// preview when its images can paint, so the swap never flickers.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ProblemError, render } from "../api/client";
import type { Document, Edit, FitInfo, ImageInfo } from "../api/types";
import { changedSpans, latestTexts } from "./log";
import { regionsFor } from "./strips";

/** The browser's own drawing of a span's text, shown until the server's render lands. */
export interface Preview {
  text: string;
  leaving: boolean; // the render has landed; the preview fades out over it
}

interface Options {
  doc: Document;
  edits: Edit[];
  scale: number;
  onExpired: () => void; // the document's hour ran out: open it again from this browser
  onProblem: (detail: string) => void;
}

export interface Strips {
  strips: ReadonlyMap<number, ImageInfo[]>; // by page
  previews: ReadonlyMap<string, Preview>; // by span
  fits: Readonly<Record<string, FitInfo>>; // the server's fit for each replaced span
  settled: (spanId: string) => void; // a preview has faded out
}

function decoded(image: ImageInfo): Promise<void> {
  const element = new Image();
  element.src = `data:image/png;base64,${image.image}`;
  // A strip that can't decode still goes in: it shows as missing, not as a stale preview.
  return element.decode().catch(() => undefined);
}

export function useStrips({ doc, edits, scale, onExpired, onProblem }: Options): Strips {
  const [strips, setStrips] = useState<ReadonlyMap<number, ImageInfo[]>>(new Map());
  const [previews, setPreviews] = useState<ReadonlyMap<string, Preview>>(new Map());
  const [fits, setFits] = useState<Readonly<Record<string, FitInfo>>>({});
  const drawn = useRef<{ docId: string; edits: Edit[] }>({ docId: doc.id, edits: [] });
  const asks = useRef(new Map<number, AbortController>());
  const spans = useMemo(() => new Map(doc.spans.map((span) => [span.id, span])), [doc]);

  // The latest callbacks, without making every render of the editor redraw pages.
  const report = useRef({ onExpired, onProblem });
  report.current = { onExpired, onProblem };

  useEffect(() => {
    // A document opened again has a new id and none of our strips: redraw every edit.
    const before = drawn.current.docId === doc.id ? drawn.current.edits : [];
    const changed = changedSpans(before, edits);
    drawn.current = { docId: doc.id, edits };
    if (changed.size === 0) return;
    const latest = latestTexts(edits);

    // Preview each changed span in its final text at once: the typed text, or the original's.
    setPreviews((now) => {
      const next = new Map(now);
      for (const id of changed) {
        const span = spans.get(id);
        if (span !== undefined) next.set(id, { text: latest.get(id) ?? span.text, leaving: false });
      }
      return next;
    });

    const onPage = (page: number) => (id: string) => spans.get(id)?.page === page;
    const leave = (page: number) =>
      setPreviews((now) => {
        const next = new Map(now);
        for (const [id, preview] of now) if (onPage(page)(id)) next.set(id, { ...preview, leaving: true });
        return next;
      });

    async function redraw(page: number) {
      asks.current.get(page)?.abort();
      const edited = doc.spans.filter((span) => span.page === page && latest.has(span.id));
      const info = doc.pages[page];
      // Nothing edited on this page any more: the original image beneath is already right.
      if (edited.length === 0 || info === undefined) {
        setStrips((now) => new Map([...now].filter(([drawnPage]) => drawnPage !== page)));
        leave(page);
        return;
      }
      const ask = new AbortController();
      asks.current.set(page, ask);
      try {
        const regions = regionsFor(page, info, edited);
        const reply = await render(doc.id, { edits, scale, regions }, ask.signal);
        const images = reply.images.filter((image) => image.page === page);
        await Promise.all(images.map(decoded));
        // A newer edit asked again while this one was out: its reply is the one to show.
        if (ask.signal.aborted) return;
        setStrips((now) => new Map(now).set(page, images));
        setFits(reply.fits);
        leave(page);
      } catch (error) {
        if (ask.signal.aborted) return;
        if (!(error instanceof ProblemError)) throw error;
        if (error.problem.status === 404) report.current.onExpired();
        else report.current.onProblem(error.problem.detail);
      }
    }

    const pages = new Set(
      [...changed].map((id) => spans.get(id)?.page).filter((page) => page !== undefined),
    );
    for (const page of pages) void redraw(page);
  }, [doc, edits, scale, spans]);

  useEffect(() => {
    const open = asks.current;
    return () => open.forEach((ask) => ask.abort());
  }, []);

  const settled = useCallback(
    (spanId: string) =>
      setPreviews((now) => {
        if (!now.get(spanId)?.leaving) return now;
        const next = new Map(now);
        next.delete(spanId);
        return next;
      }),
    [],
  );

  return { strips, previews, fits, settled };
}
