import { type RefObject, useEffect, useRef, useState } from "react";
import { pageUrl } from "../api/client";
import type { ImageInfo, PageInfo, SpanInfo } from "../api/types";
import { LAZY_MARGIN, PX_PER_PT } from "./constants";
import { boxOf, points, useEditor } from "./context";
import { EditField } from "./EditField";
import { Margin } from "./Margin";
import styles from "./Page.module.css";
import { SpanMark } from "./SpanMark";

/** Whether an element is near enough the viewport to be drawn. Pages far off stay empty boxes. */
function useNear(target: RefObject<HTMLElement | null>): boolean {
  const [near, setNear] = useState(false);
  useEffect(() => {
    const element = target.current;
    if (element === null) return;
    const watch = new IntersectionObserver(([entry]) => setNear(entry?.isIntersecting ?? false), {
      rootMargin: LAZY_MARGIN,
    });
    watch.observe(element);
    return () => watch.disconnect();
  }, [target]);
  return near;
}

/** An element's height in CSS pixels, kept current. */
function useHeight(target: RefObject<HTMLElement | null>): number {
  const [height, setHeight] = useState(0);
  useEffect(() => {
    const element = target.current;
    if (element === null) return;
    const watch = new ResizeObserver(([entry]) => setHeight(entry?.contentRect.height ?? 0));
    watch.observe(element);
    return () => watch.disconnect();
  }, [target]);
  return height;
}

interface Props {
  index: number;
  info: PageInfo;
  spans: SpanInfo[];
  strips: ImageInfo[] | undefined;
}

/** One page: its box reserved from its size, the image when near, and a mark over every span. */
export function Page({ index, info, spans, strips }: Props) {
  const editor = useEditor();
  const sheet = useRef<HTMLDivElement>(null);
  const near = useNear(sheet);
  const height = useHeight(sheet);
  // The file's /Rotate turns the page with CSS; everything inside stays in unrotated points.
  const turned = info.rotation % 180 !== 0;
  const [wide, tall] = turned ? [info.height, info.width] : [info.width, info.height];
  const changes = spans.filter((span) => {
    const now = editor.latest.get(span.id);
    return now !== undefined && now !== span.text;
  });
  const editingSpan = spans.find((span) => span.id === editor.editing?.spanId);
  const label = `Page ${index + 1}`;

  return (
    <section className={styles.page} aria-label={label}>
      <Margin info={info} spans={changes} pageHeight={height} shape="margin" label={label} />
      <div
        ref={sheet}
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
          {near && (
            <img
              className={styles.image}
              src={pageUrl(editor.doc, index, editor.scale)}
              alt=""
              onError={editor.imageFailed}
            />
          )}
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
          {spans.map((span) => (
            <Preview key={span.id} span={span} info={info} />
          ))}
          {near && spans.map((span) => <SpanMark key={span.id} span={span} info={info} />)}
          {editingSpan !== undefined && <EditField span={editingSpan} info={info} />}
        </div>
      </div>
      <Margin info={info} spans={changes} pageHeight={height} shape="list" label={label} />
    </section>
  );
}

/** The browser's drawing of a span's new text, until the server's render lands and it fades. */
function Preview({ span, info }: { span: SpanInfo; info: PageInfo }) {
  const editor = useEditor();
  const preview = editor.previews.get(span.id);
  const font = editor.fonts.get(span.font);
  if (preview === undefined || font === undefined || editor.editing?.spanId === span.id) return null;
  const box = boxOf(span.bbox, info);
  const [red = 0, green = 0, blue = 0] = span.color;
  return (
    <span
      className={preview.leaving ? styles.previewLeaving : styles.preview}
      aria-hidden="true"
      data-preview=""
      onAnimationEnd={() => editor.settled(span.id)}
      style={{
        left: box.left,
        top: box.top,
        minWidth: box.width,
        height: box.height,
        lineHeight: box.height,
        fontSize: points(span.size, info),
        color: `rgb(${red * 255} ${green * 255} ${blue * 255})`,
      }}
    >
      {preview.text}
    </span>
  );
}
