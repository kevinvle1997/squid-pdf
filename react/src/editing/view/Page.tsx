import { memo, useCallback, useMemo, useState } from "react";
import { pageUrl } from "../../api/client";
import type { PageInfo, SpanInfo } from "../../api/types";
import { LAZY_MARGIN, PX_PER_PT } from "../constants";
import { imageFailed } from "../editor";
import { familyOf, previewFaceOf } from "../faces";
import { differing, type SpanView } from "../project";
import { boxOf, points, useEditor, useEditorState } from "./context";
import { EditField } from "./EditField";
import { Margin } from "./Margin";
import styles from "./Page.module.css";
import { SpanMark } from "./SpanMark";

const NO_SPANS: readonly SpanInfo[] = [];
const NO_CHANGES: readonly SpanView[] = [];

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
  const spans = useEditorState((state) => state.layout.pages.get(index)) ?? NO_SPANS;
  const edits = useEditorState((state) => state.view.pages.get(index));
  const drawnFrom = useEditorState((state) => state.drawn.from.get(index)?.pages.get(index));
  const strips = useEditorState((state) => state.drawn.strips.get(index));
  const src = useEditorState((state) => pageUrl(state.doc, index, state.scale));
  const typingIn = useEditorState((state) => (state.draft?.page === index ? state.draft.spanId : null));
  // What every mark on the page shows, selected once here rather than by each mark.
  const fonts = useEditorState((state) => state.layout.fonts);
  const copy = useEditorState((state) => state.doc.copy);
  const fits = useEditorState((state) => state.drawn.fits);
  const quiet = useEditorState((state) => {
    const back = state.returnedTo === null ? undefined : state.layout.spans.get(state.returnedTo);
    return back?.page === index ? back.id : null;
  });
  const views = useMemo(() => new Map(edits?.spans.map((view) => [view.span.id, view])), [edits]);
  // The file's /Rotate turns the page with CSS; everything inside stays in unrotated points.
  const turned = info.rotation % 180 !== 0;
  const [wide, tall] = turned ? [info.height, info.width] : [info.width, info.height];
  const changes = edits?.spans.filter((view) => view.replaced) ?? NO_CHANGES;
  // Where a span reads other than its strip shows, the browser draws it until the server has.
  const previews = differing(edits, drawnFrom).filter(({ span }) => span.id !== typingIn);
  const label = `Page ${index + 1}`;

  return (
    <section className={styles.page} aria-label={label}>
      <Margin info={info} changes={changes} gapPt={sheet.gapPt} shape="margin" label={label} />
      <div
        ref={sheet.ref}
        className={styles.sheet}
        style={{ aspectRatio: `${wide} / ${tall}`, maxWidth: `${wide * PX_PER_PT}px` }}
      >
        <div
          className={styles.layer}
          style={{
            width: `${(info.width / wide) * 100}%`,
            height: `${(info.height / tall) * 100}%`,
            transform: `translate(-50%, -50%) rotate(${info.rotation}deg)`,
          }}
        >
          {sheet.near && <img className={styles.image} src={src} alt="" onError={() => imageFailed(editor)} />}
          {strips?.map((strip) => (
            <img
              key={strip.y}
              className={styles.strip}
              src={`data:image/png;base64,${strip.image}`}
              alt=""
              data-strip=""
              style={{ top: `${(strip.y / info.height) * 100}%` }}
            />
          ))}
          {previews.map(({ span, text }) => (
            <Preview key={span.id} span={span} text={text} info={info} />
          ))}
          {sheet.near &&
            spans.map((span) =>
              span.id === typingIn ? (
                <EditField key={span.id} span={span} info={info} />
              ) : (
                <SpanMark
                  key={span.id}
                  span={span}
                  info={info}
                  now={views.get(span.id)}
                  font={fonts.get(span.font)}
                  fit={fits[span.id]}
                  copy={copy}
                  quiet={quiet === span.id}
                />
              ),
            )}
        </div>
      </div>
      <Margin info={info} changes={changes} gapPt={sheet.gapPt} shape="list" label={label} />
    </section>
  );
});

/** The browser's drawing of a span's text, while the server's strip under it shows other words. */
function Preview({ span, text, info }: { span: SpanInfo; text: string; info: PageInfo }) {
  const font = useEditorState((state) => state.layout.fonts.get(span.font));
  if (font === undefined) return null;
  const box = boxOf(span.bbox, info);
  const [red = 0, green = 0, blue = 0] = span.color;
  return (
    <span
      className={styles.preview}
      aria-hidden="true"
      data-preview=""
      style={{
        left: box.left,
        top: box.top,
        minWidth: box.width,
        height: box.height,
        lineHeight: box.height,
        fontSize: points(span.size, info),
        fontFamily: familyOf(previewFaceOf(font)),
        color: `rgb(${red * 255} ${green * 255} ${blue * 255})`,
      }}
    >
      {text}
    </span>
  );
}
