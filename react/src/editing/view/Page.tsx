import { memo, type ReactNode, type Ref, startTransition, useCallback, useMemo, useRef, useState } from "react";
import { useFocusWithin } from "react-aria";
import type { PageInfo } from "../../api/types";
import { classes } from "../../ui/classes";
import { Trim } from "../../ui/Trim";
import { Warn } from "../../ui/Warn";
import { DOUBLE_TAP_MS, DOUBLE_TAP_REACH_PX, LAZY_MARGIN } from "../constants";
import { imageFailed, imageLoaded } from "../images";
import { openNewTextAt, pointOnPage } from "../newText";
import { type PageState, pageState, spanIn } from "../page";
import type { NewTextReading, SpanReading } from "../project";
import { stillShows } from "../redrawRegions";
import { shallowEqual } from "../store";
import { pageInView, zoomShown } from "../zoom";
import { SlipLayerContext, useEditor, useEditorState } from "./context";
import { DeletePage } from "./DeletePage";
import { boxOf, layerOf, printedPxOf, sheetShapeOf, sheetWidthOf, topOf, uprightOf } from "./geometry";
import { HowToStart } from "./HowToStart";
import { onBarePaper } from "./keys";
import { ChangeList, Margin } from "./Margin";
import { Marks } from "./Marks";
import styles from "./Page.module.css";
import { NewTextPreviews, Previews, Settles } from "./Previews";

const NO_CHANGES: readonly SpanReading[] = [];
const NO_NEW_TEXTS: readonly NewTextReading[] = [];

function useSheet(index: number, info: PageInfo, { nearPages }: { nearPages: Set<number> }) {
  const editor = useEditor();
  const heightPt = info.height;
  const [near, setNear] = useState(false);
  const [noteHeightPt, setNoteHeightPt] = useState(0);
  const ref = useCallback(
    (sheet: HTMLDivElement) => {
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
          // In the background: a page's marks are dozens of buttons, drawn in slices the scroll can go between.
          startTransition(() => setNear(isNear));
        },
        { rootMargin: LAZY_MARGIN },
      );
      const size = new ResizeObserver(([entry]) => {
        const height = entry?.contentRect.height ?? 0;
        const hit = parseFloat(getComputedStyle(sheet).getPropertyValue("--hit")) || 0;
        setNoteHeightPt(height > 0 ? (hit / height) * heightPt : 0);
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
  shownNumber: number;
}

export const Page = memo(function Page({ index, info, shownNumber }: Props) {
  const editor = useEditor();
  const sheet = useSheet(index, info, { nearPages: editor.near });
  const page = useEditorState((state) => pageState(state, index), shallowEqual);
  const changes = page.edits?.spans ?? NO_CHANGES;
  const newTexts = page.edits?.newTexts ?? NO_NEW_TEXTS;
  // A strip whose rows no longer hold an edit goes at once: the page beneath is right as it is.
  const strips = useMemo(
    () => page.strips?.filter((strip) => stillShows(strip, { edits: page.edits, info })),
    [page.strips, page.edits, info],
  );
  const label = `Page ${shownNumber}`;
  const [focusedIn, setFocusedIn] = useState(false);
  const [slipLayer, setSlipLayer] = useState<HTMLDivElement | null>(null);
  const { focusWithinProps } = useFocusWithin({ onFocusWithinChange: setFocusedIn });
  // Its marks stay far off while it's typed in, focused or sent focus: else the words or focus go, or focus comes late.
  const sentFocus = page.focusTo !== null || page.focusNewText !== null;
  const keepMarks = sheet.near || page.typingIn !== null || focusedIn || sentFocus;
  const notes = keepMarks ? changes : NO_CHANGES;
  const newTextNotes = keepMarks ? newTexts : NO_NEW_TEXTS;
  const paper = useBarePaper(index, info);

  return (
    <section
      {...focusWithinProps}
      className={styles.page}
      aria-label={label}
      style={sheetWidthOf(info)}
      data-page={index}
    >
      {/* First, so Tab reaches it after the page above and before this page's own notes and lines. */}
      <DeletePage page={index} shownNumber={shownNumber} />
      {page.howToStart && <HowToStart beside />}
      <Margin
        info={info}
        changes={notes}
        newTexts={newTextNotes}
        lines={page.layout.lines}
        noteHeightPt={sheet.noteHeightPt}
        pageNumber={shownNumber}
      />
      <Sheet info={info} sheetRef={sheet.ref} paperRef={paper} slipLayerRef={setSlipLayer}>
        {sheet.near && (
          // Decoded off the main thread, so a page coming near doesn't hold up the scroll.
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
        {sheet.near && (
          <>
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
            <NewTextPreviews page={page} info={info} strips={strips} />
          </>
        )}
        {page.scrollTo !== null && <ScrollAnchor key={page.scrollTo.count} page={page} info={info} />}
        {keepMarks && (
          <SlipLayerContext.Provider value={slipLayer}>
            <Marks page={page} info={info} />
          </SlipLayerContext.Provider>
        )}
      </Sheet>
      <ChangeList changes={notes} newTexts={newTextNotes} lines={page.layout.lines} pageNumber={shownNumber} />
    </section>
  );
});

function useBarePaper(index: number, info: PageInfo) {
  const editor = useEditor();
  const lastTap = useRef<{ at: number; x: number; y: number } | null>(null);
  return useCallback(
    (layer: HTMLDivElement) => {
      let pointer = "mouse";
      const pressAt = (event: MouseEvent) =>
        pointOnPage([event.clientX, event.clientY], { shown: layer.getBoundingClientRect(), info });
      const pressed = (event: PointerEvent) => {
        pointer = event.pointerType;
      };
      const tapped = (event: PointerEvent) => {
        if (event.pointerType !== "touch" || !onBarePaper(event.target, layer)) return;
        const at = performance.now();
        const was = lastTap.current;
        const reach = was === null ? Infinity : Math.hypot(event.clientX - was.x, event.clientY - was.y);
        const twice = was !== null && at - was.at < DOUBLE_TAP_MS && reach < DOUBLE_TAP_REACH_PX;
        lastTap.current = twice ? null : { at, x: event.clientX, y: event.clientY };
        if (twice) openNewTextAt(editor, index, pressAt(event));
      };
      const doubleClicked = (event: MouseEvent) => {
        // A finger's double tap is the taps' own, above: a browser that says it as a double click too would say it twice.
        if (pointer === "touch" || !onBarePaper(event.target, layer)) return;
        openNewTextAt(editor, index, pressAt(event));
      };
      layer.addEventListener("pointerdown", pressed);
      layer.addEventListener("pointerup", tapped);
      layer.addEventListener("dblclick", doubleClicked);
      return () => {
        layer.removeEventListener("pointerdown", pressed);
        layer.removeEventListener("pointerup", tapped);
        layer.removeEventListener("dblclick", doubleClicked);
      };
    },
    [editor, index, info],
  );
}

function Sheet({
  info,
  sheetRef,
  paperRef,
  slipLayerRef,
  children,
}: {
  info: PageInfo;
  sheetRef: Ref<HTMLDivElement>;
  paperRef: Ref<HTMLDivElement>;
  slipLayerRef: Ref<HTMLDivElement>;
  children: ReactNode;
}) {
  return (
    <>
      <div ref={sheetRef} className={styles.sheet} style={sheetShapeOf(info)}>
        <div ref={paperRef} className={styles.layer} style={layerOf(info)}>
          {children}
        </div>
      </div>
      <div className={styles.slips} style={sheetShapeOf(info)}>
        <div ref={slipLayerRef} className={styles.layer} style={layerOf(uprightOf(info))} />
      </div>
      {/* The sheet clips everything inside it, so its crop marks hang off a box of their own. */}
      <Trim className={styles.trim} />
    </>
  );
}

/** Brought into view once, as it's first drawn: the page drawing again mustn't pull the view back. */
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
