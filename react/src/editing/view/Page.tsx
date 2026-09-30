import { memo, type ReactNode, type Ref, useCallback, useMemo, useState } from "react";
import { pageUrl } from "../../api/client";
import type { Copy, FontInfo, ImageInfo, PageInfo, SpanInfo } from "../../api/types";
import { LAZY_MARGIN, PX_PER_PT } from "../constants";
import { type EditorState, imageFailed } from "../editor";
import { previewFaceOf } from "../faces";
import { differing, type PageEdits, type SpanReading } from "../project";
import type { PageFits } from "../render";
import { shallowEqual } from "../store";
import { useEditor, useEditorState } from "./context";
import { EditField } from "./EditField";
import { boxOf, shownSize, spanTextStyle } from "./geometry";
import { Margin } from "./Margin";
import styles from "./Page.module.css";
import { SpanMark } from "./SpanMark";

const NO_SPANS: readonly SpanInfo[] = [];
const NO_FITS: PageFits = {};
const NO_CHANGES: readonly SpanReading[] = [];

/**
 * Watch the sheet from the moment it mounts: whether it's near enough the viewport to draw
 * (pages far off stay empty boxes), and a margin note's height in the page's points.
 */
function useSheet(pageHeightPt: number) {
  const [near, setNear] = useState(false);
  const [gapPt, setGapPt] = useState(0);
  const ref = useCallback(
    (sheet: HTMLDivElement) => {
      const nearby = new IntersectionObserver(([entry]) => setNear(entry?.isIntersecting ?? false), {
        rootMargin: LAZY_MARGIN,
      });
      const size = new ResizeObserver(([entry]) => {
        const height = entry?.contentRect.height ?? 0;
        // A note is a hit area tall, and --hit is larger on a touch screen.
        const hit = parseFloat(getComputedStyle(sheet).getPropertyValue("--hit")) || 0;
        setGapPt(height > 0 ? (hit / height) * pageHeightPt : 0);
      });
      nearby.observe(sheet);
      size.observe(sheet);
      return () => {
        nearby.disconnect();
        size.disconnect();
      };
    },
    [pageHeightPt],
  );
  return { ref, near, gapPt };
}

/** Everything a page draws from the editor's state: it redraws when one of these changes. */
interface PageState {
  readonly spans: readonly SpanInfo[]; // its text, in the document's order
  readonly edits: PageEdits | undefined; // what the history reads as, here
  readonly drawnFrom: PageEdits | undefined; // what its strips were drawn from
  readonly strips: readonly ImageInfo[] | undefined;
  readonly fits: PageFits;
  readonly src: string; // the page image
  readonly typingIn: string | null; // the span being typed into, when it's here
  readonly quiet: string | null; // the span focus went back to after its edit, when it's here
  // What every mark shows, selected once here rather than by each mark.
  readonly fonts: ReadonlyMap<string, FontInfo>;
  readonly copy: Copy;
}

function pageState(state: EditorState, index: number): PageState {
  const back = state.returnedTo === null ? undefined : state.layout.spans.get(state.returnedTo);
  return {
    spans: state.layout.pages.get(index) ?? NO_SPANS,
    edits: state.reading.pages.get(index),
    drawnFrom: state.drawn.from.get(index)?.pages.get(index),
    strips: state.drawn.strips.get(index),
    fits: state.drawn.fits.get(index) ?? NO_FITS,
    src: pageUrl(state.doc, index, state.scale),
    typingIn: state.draft?.page === index ? state.draft.spanId : null,
    quiet: back?.page === index ? back.id : null,
    fonts: state.layout.fonts,
    copy: state.doc.copy,
  };
}

interface Props {
  index: number;
  info: PageInfo;
}

/**
 * One page: its box reserved from its size, the image when near, and a mark over every span.
 * It redraws only when something on it changes: its edits, its strips, or the span being typed in.
 */
export const Page = memo(function Page({ index, info }: Props) {
  const editor = useEditor();
  const sheet = useSheet(info.height);
  const page = useEditorState((state) => pageState(state, index), shallowEqual);
  const changes = page.edits?.spans.filter((reading) => reading.replaced) ?? NO_CHANGES;
  const [wide] = shownSize(info);
  const label = `Page ${index + 1}`;

  return (
    // The sheet's printed width, which the page's own column and max width are built from.
    <section className={styles.page} aria-label={label} style={{ "--sheet-width": `${wide * PX_PER_PT}px` }}>
      <Margin info={info} changes={changes} gapPt={sheet.gapPt} shape="margin" label={label} />
      <Sheet info={info} sheetRef={sheet.ref}>
        {sheet.near && <img className={styles.image} src={page.src} alt="" onError={() => imageFailed(editor)} />}
        {page.strips?.map((strip) => (
          <img
            key={strip.y}
            className={styles.strip}
            src={`data:image/png;base64,${strip.image}`}
            alt=""
            data-strip=""
            style={{ top: `${(strip.y / info.height) * 100}%` }}
          />
        ))}
        <Previews page={page} info={info} />
        {sheet.near && <Marks page={page} info={info} />}
      </Sheet>
      <Margin info={info} changes={changes} gapPt={sheet.gapPt} shape="list" label={label} />
    </section>
  );
});

/** The sheet, as wide as the page is printed and turned as the file says; inside, everything is in unrotated points. */
function Sheet({ info, sheetRef, children }: { info: PageInfo; sheetRef: Ref<HTMLDivElement>; children: ReactNode }) {
  const [wide, tall] = shownSize(info);
  return (
    <div ref={sheetRef} className={styles.sheet} style={{ aspectRatio: `${wide} / ${tall}` }}>
      <div
        className={styles.layer}
        style={{
          width: `${(info.width / wide) * 100}%`,
          height: `${(info.height / tall) * 100}%`,
          transform: `translate(-50%, -50%) rotate(${info.rotation}deg)`,
        }}
      >
        {children}
      </div>
    </div>
  );
}

/** Where a span reads other than its strip shows, the browser draws it until the server has. */
function Previews({ page, info }: { page: PageState; info: PageInfo }) {
  const previews = differing(page.edits, page.drawnFrom).filter(({ span }) => span.id !== page.typingIn);
  return previews.map(({ span, text }) => (
    <Preview key={span.id} span={span} text={text} info={info} font={page.fonts.get(span.font)} />
  ));
}

/** A mark over every span, or the field where one is being typed into. */
function Marks({ page, info }: { page: PageState; info: PageInfo }) {
  const readings = useMemo(() => new Map(page.edits?.spans.map((reading) => [reading.span.id, reading])), [page.edits]);
  return page.spans.map((span) =>
    span.id === page.typingIn ? (
      <EditField key={span.id} span={span} info={info} />
    ) : (
      <SpanMark
        key={span.id}
        span={span}
        info={info}
        edited={readings.get(span.id)}
        font={page.fonts.get(span.font)}
        fit={page.fits[span.id]}
        copy={page.copy}
        quiet={page.quiet === span.id}
      />
    ),
  );
}

/** The browser's drawing of a span's text, while the server's strip under it shows other words. */
function Preview({
  span,
  text,
  info,
  font,
}: {
  span: SpanInfo;
  text: string;
  info: PageInfo;
  font: FontInfo | undefined;
}) {
  if (font === undefined) return null;
  const box = boxOf(span.bbox, info);
  return (
    <span
      className={styles.preview}
      aria-hidden="true"
      data-preview=""
      style={{ ...spanTextStyle(span, info, previewFaceOf(font)), minWidth: box.width, lineHeight: box.height }}
    >
      {text}
    </span>
  );
}
