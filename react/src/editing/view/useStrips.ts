// The server's render of each edited row, redrawn when the edit list changes.
// Typing never waits on it: while a span's text differs from what its strip shows, the
// browser previews it; the strip goes in only once its images can paint, so the swap
// never flickers.
import { useRef, useState } from "react";
import { ProblemError, render } from "../../api/client";
import type { Document, FitInfo, ImageInfo, Region } from "../../api/types";
import type { Reopener } from "../../documents/reopen";
import { type EditedView, samePage } from "../project";
import { regionsFor } from "../strips";

interface Options {
  scale: number;
  reopener: Reopener; // the document's hour ran out: open it again from this browser
  onProblem: (detail: string) => void;
}

export interface Strips {
  strips: ReadonlyMap<number, ImageInfo[]>; // by page
  shown: ReadonlyMap<string, string>; // by span: the text its strip shows, when it has one
  fits: Readonly<Record<string, FitInfo>>; // the server's fit for each replaced span
  redraw: (doc: Document, before: EditedView, after: EditedView) => void; // the edits changed
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

export function useStrips({ scale, reopener, onProblem }: Options): Strips {
  const [strips, setStrips] = useState<ReadonlyMap<number, ImageInfo[]>>(new Map());
  const [shown, setShown] = useState<ReadonlyMap<string, string>>(new Map());
  const [fits, setFits] = useState<Readonly<Record<string, FitInfo>>>({});
  // One request at a time, so an older reply can never land over a newer one.
  const asking = useRef<Asking | null>(null);

  // What the strips on `pages` show now: each span's text in `view`, or the original's.
  const showing = (doc: Document, pages: ReadonlySet<number>, view: EditedView) =>
    setShown((now) => {
      const next = new Map(now);
      for (const span of doc.spans) {
        if (!pages.has(span.page)) continue;
        const text = view.spans.get(span.id)?.text;
        if (text === undefined) next.delete(span.id);
        else next.set(span.id, text);
      }
      return next;
    });

  async function draw(doc: Document, view: EditedView, wanted: ReadonlySet<number>) {
    // A newer change takes over the pages an older request was still drawing.
    const pages = new Set(wanted);
    const before = asking.current;
    before?.ask.abort();
    before?.pages.forEach((page) => pages.add(page));

    const regions: Region[] = [];
    const bare = new Set<number>();
    for (const page of pages) {
      const edited = view.pages.get(page);
      const info = doc.pages[page];
      // Nothing edited on this page any more: the original image beneath is already right.
      if (edited === undefined || info === undefined) bare.add(page);
      else regions.push(...regionsFor(page, info, edited));
    }
    if (bare.size > 0) {
      setStrips((now) => new Map([...now].filter(([page]) => !bare.has(page))));
      showing(doc, bare, view);
    }
    const drawing = new Set([...pages].filter((page) => !bare.has(page)));
    if (drawing.size === 0) {
      asking.current = null;
      return;
    }

    const ask = new AbortController();
    asking.current = { ask, pages: drawing };
    try {
      // Still ours while the document opens again, so a newer change can take these pages over.
      const reply = await reopener.withDocument((current) =>
        render(current.id, { edits: [...view.edits], scale, regions }, ask.signal),
      );
      await Promise.all(reply.images.map(decoded));
      if (ask.signal.aborted) return;
      asking.current = null;
      setStrips((now) => {
        const next = new Map(now);
        for (const page of drawing) next.set(page, reply.images.filter((image) => image.page === page));
        return next;
      });
      setFits(reply.fits);
      showing(doc, drawing, view);
    } catch (error) {
      if (ask.signal.aborted) return;
      if (!(error instanceof ProblemError)) throw error;
      asking.current = null;
      onProblem(error.problem.detail);
    }
  }

  function redraw(doc: Document, before: EditedView, after: EditedView) {
    const touched = new Set([...before.pages.keys(), ...after.pages.keys()]);
    const pages = new Set([...touched].filter((page) => !samePage(before.pages.get(page), after.pages.get(page))));
    if (pages.size > 0) void draw(doc, after, pages);
  }

  return { strips, shown, fits, redraw };
}
