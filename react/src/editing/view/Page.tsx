import { memo, type ReactNode, type Ref, useCallback, useMemo, useState } from "react";
import { useFocusWithin } from "react-aria";
import type { PageInfo } from "../../api/types";
import { classes } from "../../ui/classes";
import { Trim } from "../../ui/Trim";
import { Warn } from "../../ui/Warn";
import { LAZY_MARGIN } from "../constants";
import { imageFailed, imageLoaded } from "../images";
import { type PageState, pageState, spanIn } from "../page";
import type { SpanReading } from "../project";
import { shallowEqual } from "../store";
import { stillShows } from "../strips";
import { pageInView, zoomShown } from "../zoom";
import { useEditor, useEditorState } from "./context";
import { boxOf, layerOf, printedPxOf, sheetShapeOf, sheetWidthOf, topOf } from "./geometry";
import { Hint } from "./Hint";
import { ChangeList, Margin } from "./Margin";
import { Marks } from "./Marks";
import styles from "./Page.module.css";
import { Previews, Settles } from "./Previews";

const NO_CHANGES: readonly SpanReading[] = [];

/**
 * Watch the sheet from the moment it mounts: whether it's near enough to draw, and a note's height in points;
 * whether it's the page under the window's middle; and, the first page, the share of its printed size it shows at.
 *
 * Pages far off stay empty boxes. The pages near are in `nearPages` too, for the render to ask for first.
 */
function useSheet(index: number, info: PageInfo, { nearPages }: { nearPages: Set<number> }) {
  const editor = useEditor();
  const heightPt = info.height;
  const [near, setNear] = useState(false);
  const [noteHeightPt, setNoteHeightPt] = useState(0);
  const ref = useCallback(
    (sheet: HTMLDivElement) => {
      // A line across the window's middle: the page it crosses is the one being read.
      const middle = new IntersectionObserver(
        ([entry]) => {
          if (entry?.isIntersecting) pageInView(editor, index);
        },
        { rootMargin: "-50% 0px -50% 0px" },
      );
      const nearby = new IntersectionObserver(
        ([entry]) => {
          const isNear = entry?.isIntersecting ?? false;
          if (isNear) nearPages.add(index);
          else nearPages.delete(index);
          setNear(isNear);
        },
        { rootMargin: LAZY_MARGIN },
      );
      const size = new ResizeObserver(([entry]) => {
        const height = entry?.contentRect.height ?? 0;
        // A note is a hit area tall, and --hit is larger on a touch screen.
        const hit = parseFloat(getComputedStyle(sheet).getPropertyValue("--hit")) || 0;
        setNoteHeightPt(height > 0 ? (hit / height) * heightPt : 0);
        // The first page says what share of its printed size the pages show at, for the zoom to say and step from.
        const width = entry?.contentRect.width ?? 0;
        if (index === 0 && width > 0) zoomShown(editor, width / printedPxOf(info));
      });
      nearby.observe(sheet);
      middle.observe(sheet);
      size.observe(sheet);
      return () => {
        nearby.disconnect();
        middle.disconnect();
        size.disconnect();
        nearPages.delete(index);
      };
    },
    [editor, index, info, heightPt, nearPages],
  );
  return { ref, near, noteHeightPt };
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
  const sheet = useSheet(index, info, { nearPages: editor.near });
  const page = useEditorState((state) => pageState(state, index), shallowEqual);
  const changes = page.edits?.spans ?? NO_CHANGES;
  // A strip whose rows no longer hold an edit goes at once: the page beneath is right as it is, and
  // undo needs no render to show it.
  const strips = useMemo(
    () => page.strips?.filter((strip) => stillShows(strip, { edits: page.edits, info })),
    [page.strips, page.edits, info],
  );
  const label = `Page ${index + 1}`;
  const [focusedIn, setFocusedIn] = useState(false);
  const { focusWithinProps } = useFocusWithin({ onFocusWithinChange: setFocusedIn });
  // The page being typed in keeps its marks, and so the field, however far it's scrolled; so does the
  // page whose mark has focus, or focus would fall to the document as its mark went.
  const keepMarks = sheet.near || page.typingIn !== null || focusedIn;

  return (
    // The sheet's printed width, which the page's own column and max width are built from.
    <section {...focusWithinProps} className={styles.page} aria-label={label} style={sheetWidthOf(info)}>
      <Margin info={info} changes={changes} noteHeightPt={sheet.noteHeightPt} pageNumber={index + 1} />
      {index === 0 && <Hint where="over" />}
      <Sheet info={info} sheetRef={sheet.ref}>
        {sheet.near && (
          // Decoded off the main thread, so a page coming near doesn't hold up the scroll. Drawn anew for each try.
          <img
            key={page.image?.tries ?? 0}
            className={classes(styles.image, page.image?.failed != null && styles.missing)}
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
        {strips?.map((strip) => (
          <img
            key={strip.y}
            className={styles.strip}
            src={strip.src}
            alt=""
            data-strip=""
            style={topOf(strip.y, info)}
          />
        ))}
        <Settles page={page} info={info} />
        <Previews page={page} info={info} strips={strips} />
        {page.scrollTo !== null && <ScrollAnchor key={page.scrollTo.count} page={page} info={info} />}
        {keepMarks && <Marks page={page} info={info} />}
      </Sheet>
      <ChangeList changes={changes} pageNumber={index + 1} />
    </section>
  );
});

/** The sheet, as wide as the page is printed and turned as the file says; inside, everything is in unrotated points. */
function Sheet({ info, sheetRef, children }: { info: PageInfo; sheetRef: Ref<HTMLDivElement>; children: ReactNode }) {
  return (
    <>
      <div ref={sheetRef} className={styles.sheet} style={sheetShapeOf(info)}>
        <div className={styles.layer} style={layerOf(info)}>
          {children}
        </div>
      </div>
      {/* Its crop marks, in its cell: the sheet clips everything inside it, so they hang off a box of their own. */}
      <Trim className={styles.trim} />
    </>
  );
}

/**
 * Over the span undo or redo changed, or find went to, brought into view once, as it's first drawn: it
 * stays, and the page drawing again mustn't pull the view back. Nothing scrolls if it's in view.
 */
function ScrollAnchor({ page, info }: { page: PageState; info: PageInfo }) {
  const block = page.scrollTo?.block ?? "nearest";
  const bringIntoView = useCallback(
    (anchor: HTMLSpanElement | null) => {
      anchor?.scrollIntoView({ block, inline: "nearest" });
    },
    [block],
  );
  const span = page.scrollTo === null ? undefined : spanIn(page, page.scrollTo.spanId);
  if (span === undefined) return null;
  return <span className={styles.anchor} aria-hidden="true" style={boxOf(span.bbox, info)} ref={bringIntoView} />;
}
