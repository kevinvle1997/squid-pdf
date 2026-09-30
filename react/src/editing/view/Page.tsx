import { useCallback, useState } from "react";
import { pageUrl } from "../../api/client";
import type { ImageInfo, PageInfo, SpanInfo } from "../../api/types";
import { LAZY_MARGIN, PX_PER_PT } from "../constants";
import { boxOf, points, useEditor } from "./context";
import { EditField } from "./EditField";
import { familyOf, previewFaceOf } from "../faces";
import { Margin } from "./Margin";
import styles from "./Page.module.css";
import { SpanMark } from "./SpanMark";

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
  spans: SpanInfo[];
  strips: readonly ImageInfo[] | undefined;
}

/** One page: its box reserved from its size, the image when near, and a mark over every span. */
export function Page({ index, info, spans, strips }: Props) {
  const editor = useEditor();
  const sheet = useSheet(info.height);
  // The file's /Rotate turns the page with CSS; everything inside stays in unrotated points.
  const turned = info.rotation % 180 !== 0;
  const [wide, tall] = turned ? [info.height, info.width] : [info.width, info.height];
  const changes = spans.filter((span) => {
    const now = editor.latest.get(span.id);
    return now !== undefined && now !== span.text;
  });
  const label = `Page ${index + 1}`;

  return (
    <section className={styles.page} aria-label={label}>
      <Margin info={info} spans={changes} gapPt={sheet.gapPt} shape="margin" label={label} />
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
          {sheet.near && (
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
          {sheet.near &&
            spans.map((span) =>
              span.id === editor.editing?.spanId ? (
                <EditField key={span.id} span={span} info={info} text={editor.editing.text} />
              ) : (
                <SpanMark key={span.id} span={span} info={info} />
              ),
            )}
        </div>
      </div>
      <Margin info={info} spans={changes} gapPt={sheet.gapPt} shape="list" label={label} />
    </section>
  );
}

/** The browser's drawing of a span's text, while the server's strip under it shows other words. */
function Preview({ span, info }: { span: SpanInfo; info: PageInfo }) {
  const editor = useEditor();
  const font = editor.fonts.get(span.font);
  const text = editor.latest.get(span.id) ?? span.text;
  const drawn = editor.shown.get(span.id) ?? span.text;
  if (text === drawn || font === undefined || editor.editing?.spanId === span.id) return null;
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
