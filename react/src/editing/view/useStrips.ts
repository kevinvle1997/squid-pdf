// The server's render of each edited row, redrawn when the edit list changes.
// Typing never waits on it: while a span's text differs from what its strip shows, the
// browser previews it; the strip goes in only once its images can paint, so the swap
// never flickers.
import { useRef, useState } from "react";
import { ProblemError, render } from "../../api/client";
import type { Document, Edit, FitInfo, ImageInfo, Region } from "../../api/types";
import { changedSpans, latestTexts } from "../log";
import { regionsFor } from "../strips";

interface Options {
  scale: number;
  reopen: () => Promise<Document | null>; // the document's hour ran out: open it again from this browser
  onProblem: (detail: string) => void;
}

export interface Strips {
  strips: ReadonlyMap<number, ImageInfo[]>; // by page
  shown: ReadonlyMap<string, string>; // by span: the text its strip shows, when it has one
  fits: Readonly<Record<string, FitInfo>>; // the server's fit for each replaced span
  redraw: (doc: Document, before: readonly Edit[], after: Edit[]) => void; // the edit list changed
}

interface Asking {
  ask: AbortController;
  pages: ReadonlySet<number>;
}

function decoded(image: ImageInfo): Promise<void> {
  const element = new Image();
  element.src = `data:image/png;base64,${image.image}`;
  // A strip that can't decode still goes in: it shows as missing, not as a stale preview.
  return element.decode().catch(() => undefined);
}

export function useStrips({ scale, reopen, onProblem }: Options): Strips {
  const [strips, setStrips] = useState<ReadonlyMap<number, ImageInfo[]>>(new Map());
  const [shown, setShown] = useState<ReadonlyMap<string, string>>(new Map());
  const [fits, setFits] = useState<Readonly<Record<string, FitInfo>>>({});
  // One request at a time, so an older reply can never land over a newer one.
  const asking = useRef<Asking | null>(null);

  // What the strips on `pages` show now: each span's text in `latest`, or the original's.
  const showing = (doc: Document, pages: ReadonlySet<number>, latest: ReadonlyMap<string, string>) =>
    setShown((now) => {
      const next = new Map(now);
      for (const span of doc.spans) {
        if (!pages.has(span.page)) continue;
        const text = latest.get(span.id);
        if (text === undefined) next.delete(span.id);
        else next.set(span.id, text);
      }
      return next;
    });

  async function draw(doc: Document, edits: Edit[], wanted: ReadonlySet<number>) {
    // A newer change takes over the pages an older request was still drawing.
    const pages = new Set(wanted);
    const before = asking.current;
    before?.ask.abort();
    before?.pages.forEach((page) => pages.add(page));

    const latest = latestTexts(edits);
    const regions: Region[] = [];
    const bare = new Set<number>();
    for (const page of pages) {
      const edited = doc.spans.filter((span) => span.page === page && latest.has(span.id));
      const info = doc.pages[page];
      // Nothing edited on this page any more: the original image beneath is already right.
      if (edited.length === 0 || info === undefined) bare.add(page);
      else regions.push(...regionsFor(page, info, edited));
    }
    if (bare.size > 0) {
      setStrips((now) => new Map([...now].filter(([page]) => !bare.has(page))));
      showing(doc, bare, latest);
    }
    const drawing = new Set([...pages].filter((page) => !bare.has(page)));
    if (drawing.size === 0) {
      asking.current = null;
      return;
    }

    const ask = new AbortController();
    asking.current = { ask, pages: drawing };
    try {
      const reply = await render(doc.id, { edits, scale, regions }, ask.signal);
      await Promise.all(reply.images.map(decoded));
      if (ask.signal.aborted) return;
      asking.current = null;
      setStrips((now) => {
        const next = new Map(now);
        for (const page of drawing) next.set(page, reply.images.filter((image) => image.page === page));
        return next;
      });
      setFits(reply.fits);
      showing(doc, drawing, latest);
    } catch (error) {
      if (ask.signal.aborted) return;
      if (!(error instanceof ProblemError)) throw error;
      if (error.problem.status !== 404) {
        asking.current = null;
        onProblem(error.problem.detail);
        return;
      }
      // Still ours while the document opens again, so a newer change can take these pages over.
      const again = await reopen();
      if (again === null || ask.signal.aborted) return;
      asking.current = null;
      await draw(again, edits, drawing);
    }
  }

  function redraw(doc: Document, before: readonly Edit[], after: Edit[]) {
    const changed = changedSpans(before, after);
    const pages = new Set(doc.spans.filter((span) => changed.has(span.id)).map((span) => span.page));
    if (pages.size > 0) void draw(doc, after, pages);
  }

  return { strips, shown, fits, redraw };
}
