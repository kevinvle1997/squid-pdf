import { memo, type ReactNode, type Ref, useCallback, useId, useMemo, useState, useSyncExternalStore } from "react";
import { useFocusVisible, useFocusWithin } from "react-aria";
import { pageUrl } from "../../api/client";
import type { Copy, FontInfo, PageInfo, SpanInfo } from "../../api/types";
import { Tooltip } from "../../ui/Tooltip";
import { Warn } from "../../ui/Warn";
import { LAZY_MARGIN, PX_PER_PT } from "../constants";
import type { EditorState, FocusTo, ScrollTo } from "../editor";
import { previewFaceOf } from "../faces";
import { imageFailed, imageLoaded, type PageImage } from "../images";
import { lookOf } from "../marks";
import { differing, type PageEdits, type SpanReading } from "../project";
import type { PageFits, PageRedactions, Strip } from "../render";
import type { Settling } from "../settle";
import { shallowEqual } from "../store";
import { useEditor, useEditorState } from "./context";
import { EditField } from "./EditField";
import { boxOf, points, shownSize, spanTextStyle } from "./geometry";
import { Margin } from "./Margin";
import { createNotes, type Notes } from "./notes";
import styles from "./Page.module.css";
import { SpanMark } from "./SpanMark";
import { UndoRedaction } from "./UndoRedaction";

const NO_SPANS: readonly SpanInfo[] = [];
const NO_FITS: PageFits = {};
const NO_REDACTIONS: PageRedactions = {};
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
  readonly strips: readonly Strip[] | undefined;
  readonly fits: PageFits;
  readonly redactions: PageRedactions; // the server's verdict on each redaction drawn here
  readonly hits: ReadonlySet<string> | undefined; // the spans find found here, while it's open
  readonly settling: Settling | undefined; // the spans its last render disagreed with, to pulse
  readonly src: string; // the page image
  readonly image: PageImage | undefined; // why its image failed, and how often it's been asked for again
  readonly typingIn: string | null; // the span being typed into, when it's here
  readonly unredacting: string | null; // the redacted span asking before it's edited, when it's here
  readonly focusTo: FocusTo | null; // the span focus is sent to, when it's here
  readonly scrollTo: ScrollTo | null; // the span brought into view, when it's here
  // What every mark shows, selected once here rather than by each mark.
  readonly fonts: ReadonlyMap<string, FontInfo>;
  readonly copy: Copy;
}

function pageState(state: EditorState, index: number): PageState {
  return {
    spans: state.layout.pages.get(index) ?? NO_SPANS,
    edits: state.reading.pages.get(index),
    drawnFrom: state.drawn.from.get(index)?.pages.get(index),
    strips: state.drawn.strips.get(index),
    fits: state.drawn.fits.get(index) ?? NO_FITS,
    redactions: state.drawn.redactions.get(index) ?? NO_REDACTIONS,
    hits: state.find.hits.get(index),
    settling: state.settling.get(index),
    src: pageUrl(state.doc, index, state.scale),
    image: state.images.get(index),
    typingIn: state.draft?.page === index ? state.draft.spanId : null,
    unredacting:
      state.unredacting !== null && state.layout.spans.get(state.unredacting.spanId)?.page === index
        ? state.unredacting.spanId
        : null,
    focusTo:
      state.focusTo !== null && state.layout.spans.get(state.focusTo.spanId)?.page === index ? state.focusTo : null,
    scrollTo:
      state.scrollTo !== null && state.layout.spans.get(state.scrollTo.spanId)?.page === index ? state.scrollTo : null,
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
  const [focusedIn, setFocusedIn] = useState(false);
  const { focusWithinProps } = useFocusWithin({ onFocusWithinChange: setFocusedIn });
  // The page being typed in keeps its marks, and so the field, however far it's scrolled; so does the
  // page whose mark has focus, or focus would fall to the document as its mark went.
  const keepMarks = sheet.near || page.typingIn !== null || focusedIn;

  return (
    // The sheet's printed width, which the page's own column and max width are built from.
    <section
      {...focusWithinProps}
      className={styles.page}
      aria-label={label}
      style={{ "--sheet-width": `${wide * PX_PER_PT}px` }}
    >
      <Margin info={info} changes={changes} gapPt={sheet.gapPt} shape="margin" label={label} />
      <Sheet info={info} sheetRef={sheet.ref}>
        {sheet.near && (
          // Decoded off the main thread, so a page coming near doesn't hold up the scroll. Drawn anew for each try.
          <img
            key={page.image?.tries ?? 0}
            className={page.image?.failed == null ? styles.image : `${styles.image} ${styles.missing}`}
            src={page.src}
            alt=""
            decoding="async"
            onLoad={() => imageLoaded(editor, index)}
            onError={() => void imageFailed(editor, index)}
          />
        )}
        {page.image?.failed != null && (
          <p className={styles.failed}>
            <Warn mark>{page.image.failed}</Warn>
          </p>
        )}
        {page.strips?.map((strip) => (
          <img
            key={strip.y}
            className={styles.strip}
            src={strip.src}
            alt=""
            data-strip=""
            style={{ top: `${(strip.y / info.height) * 100}%` }}
          />
        ))}
        <Settles page={page} info={info} />
        <Previews page={page} info={info} />
        {page.scrollTo !== null && <ScrollAnchor key={page.scrollTo.count} page={page} info={info} />}
        {keepMarks && <Marks page={page} info={info} />}
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
          transform: `translate(-50%, -50%) rotate(${info.turn_cw}deg)`,
        }}
      >
        {children}
      </div>
    </div>
  );
}

/**
 * Over the span undo or redo changed, brought into view once, as it's first drawn: it stays,
 * and the page drawing again mustn't pull the view back. Nothing scrolls if it's in view.
 */
function ScrollAnchor({ page, info }: { page: PageState; info: PageInfo }) {
  const bringIntoView = useCallback((anchor: HTMLSpanElement | null) => {
    anchor?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, []);
  const span = page.spans.find((each) => each.id === page.scrollTo?.spanId);
  if (span === undefined) return null;
  return <span className={styles.anchor} aria-hidden="true" style={boxOf(span.bbox, info)} ref={bringIntoView} />;
}

/** Where the render said other than the preview, its words come up from faint as it lands: drawn anew each landing. */
function Settles({ page, info }: { page: PageState; info: PageInfo }) {
  const { settling } = page;
  if (settling === undefined) return null;
  return page.spans
    .filter((span) => settling.spanIds.has(span.id))
    .map((span) => (
      <span
        key={`${span.id}:${settling.count}`}
        className={styles.settle}
        aria-hidden="true"
        data-settle=""
        style={boxOf(span.bbox, info)}
      />
    ));
}

/** Where a span reads other than its strip shows, the browser draws it until the server has. */
function Previews({ page, info }: { page: PageState; info: PageInfo }) {
  const previews = differing(page.edits, page.drawnFrom).filter(({ span }) => span.id !== page.typingIn);
  return previews.map(({ span, text, redacted }) =>
    // A redaction leaves the page bare where its words were, as the download will.
    redacted ? (
      <span
        key={span.id}
        className={styles.preview}
        aria-hidden="true"
        data-preview=""
        style={boxOf(span.bbox, info)}
      />
    ) : (
      <Preview key={span.id} span={span} text={text} info={info} font={page.fonts.get(span.font)} />
    ),
  );
}

/** A mark over every span, or the field where one is being typed into, and the one note they share. */
function Marks({ page, info }: { page: PageState; info: PageInfo }) {
  const [notes] = useState(createNotes);
  const noteId = useId();
  const readings = useMemo(() => new Map(page.edits?.spans.map((reading) => [reading.span.id, reading])), [page.edits]);
  return (
    <>
      {page.spans.map((span) =>
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
            redaction={page.redactions[span.id]}
            hit={page.hits?.has(span.id) ?? false}
            copy={page.copy}
            focusTo={page.focusTo?.spanId === span.id ? page.focusTo : null}
            notes={notes}
            noteId={noteId}
          />
        ),
      )}
      <PageNote notes={notes} id={noteId} page={page} readings={readings} />
      {page.unredacting !== null && (
        <UndoRedactionAt spanId={page.unredacting} page={page} info={info} readings={readings} />
      )}
    </>
  );
}

/** The note of the span the page's marks say, over its mark; kept while it closes, for its animation. */
function PageNote(props: { notes: Notes; id: string; page: PageState; readings: ReadonlyMap<string, SpanReading> }) {
  const { notes, id, page, readings } = props;
  const { shown, last } = useSyncExternalStore(notes.store.subscribe, notes.store.get);
  // Focus shows the note only when it's the keyboard's: a click's focus leaves it to the click.
  const { isFocusVisible } = useFocusVisible();
  const span = last === null ? undefined : page.spans.find((each) => each.id === last.spanId);
  if (last === null || span === undefined) return null;
  const { note } = lookOf({
    span,
    font: page.fonts.get(span.font),
    edited: readings.get(span.id),
    fit: page.fits[span.id],
    redaction: page.redactions[span.id],
    copy: page.copy,
  });
  if (note === null) return null;
  return (
    <Tooltip
      key={last.spanId}
      id={id}
      triggerRef={{ current: last.anchor }}
      isOpen={shown !== null && (shown.via !== "focus" || isFocusVisible)}
      onOpenChange={(isOpen) => isOpen || notes.hide(last.spanId)}
      heading={note.said}
      warn={note.warn}
    >
      {note.why}
    </Tooltip>
  );
}

/** The prompt beside a redacted span the user asked to edit, while it's still redacted. */
function UndoRedactionAt(props: {
  spanId: string;
  page: PageState;
  info: PageInfo;
  readings: ReadonlyMap<string, SpanReading>;
}) {
  const { spanId, page, info, readings } = props;
  const span = page.spans.find((each) => each.id === spanId);
  if (span === undefined || !readings.get(spanId)?.redacted) return null;
  return <UndoRedaction span={span} info={info} copy={page.copy} />;
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
  // A length, not the box's share of the page: CSS reads a line height in percent against the font size.
  const line = points(span.bbox.y1 - span.bbox.y0, info);
  return (
    <span
      className={styles.preview}
      aria-hidden="true"
      data-preview=""
      style={{ ...spanTextStyle(span, info, previewFaceOf(font)), minWidth: box.width, lineHeight: line }}
    >
      {text}
    </span>
  );
}
