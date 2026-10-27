// What the browser draws over a page until the server's render shows it: a span's new words, a
// redaction's bare paper, the page's own pixels where a span is put back, and the settle as a render lands.
import type { FitRules, FontInfo, PageInfo, SpanInfo, Strategy } from "../../api/types";
import { STRIP_PAD_PT } from "../constants";
import { previewFaceOf } from "../faces";
import { settingOf } from "../fit";
import { textFaceOf } from "../newText";
import type { PageState } from "../page";
import { differing } from "../project";
import { isUnderStrip, stripBoxOf } from "../redrawRegions";
import type { Strip } from "../render";
import { fontOf } from "../spanFonts";
import { boxOf, cropOf, previewStyleOf, textBoxIn, textStyleOf } from "./geometry";
import styles from "./Previews.module.css";

/** Where the render said other than the preview, its words come up from faint as it lands: drawn anew each landing. */
export function Settles({ page, info }: { page: PageState; info: PageInfo }) {
  const { disagreement } = page;
  if (disagreement === undefined) return null;
  const spans = page.spans
    .filter((span) => disagreement.spanIds.has(span.id))
    .map((span) => ({ key: span.id, box: span.bbox }));
  const texts = (page.edits?.inserts ?? [])
    .filter((text) => disagreement.textIds.has(text.textId))
    .map((text) => ({ key: `text ${text.textId}`, box: textBoxIn(page.layout, text.edit) }));
  return [...spans, ...texts].map(({ key, box }) => (
    <span
      key={`${key}:${disagreement.count}`}
      className={styles.settle}
      aria-hidden="true"
      data-settle=""
      style={boxOf(box, info)}
    />
  ));
}

/**
 * New text the page's strips don't show as it is now: drawn by the browser in the face the server will draw it in,
 * over whatever the page has there, as it exports. Where a strip still shows an earlier version, moved or retyped
 * since, the page's own pixels cover it until the render lands.
 */
export function TextPreviews({
  page,
  info,
  strips,
}: {
  page: PageState;
  info: PageInfo;
  strips: readonly Strip[] | undefined;
}) {
  const now = page.edits?.inserts ?? [];
  const drawn = page.drawnFrom?.inserts ?? [];
  const drawnIds = new Set(drawn.map((text) => text.id));
  const nowIds = new Set(now.map((text) => text.id));
  const covered = drawn.filter(
    (text) => !nowIds.has(text.id) && isUnderStrip(textBoxIn(page.layout, text.edit), { strips, info }),
  );
  const previewed = now.filter((text) => !drawnIds.has(text.id) && text.textId !== page.writingAgain);
  return (
    <>
      {covered.map((text) => (
        <Original
          key={`was ${text.id}`}
          box={padded(textBoxIn(page.layout, text.edit), info)}
          info={info}
          src={page.src}
        />
      ))}
      {previewed.map((text) => (
        <span
          key={text.textId}
          className={styles.textPreview}
          aria-hidden="true"
          data-preview=""
          style={textStyleOf(text.edit, info, textFaceOf(page.layout, text.edit))}
        >
          {text.edit.text}
        </span>
      ))}
    </>
  );
}

/** A box as far past its edges as a strip reaches, inside the page: what the server redraws round new text. */
function padded(box: { x0: number; y0: number; x1: number; y1: number }, info: PageInfo) {
  return {
    x0: Math.max(0, box.x0 - STRIP_PAD_PT),
    y0: Math.max(0, box.y0 - STRIP_PAD_PT),
    x1: Math.min(info.width, box.x1 + STRIP_PAD_PT),
    y1: Math.min(info.height, box.y1 + STRIP_PAD_PT),
  };
}

/** Where a span reads other than its strip shows, the browser draws it until the server has. */
export function Previews({
  page,
  info,
  strips,
}: {
  page: PageState;
  info: PageInfo;
  strips: readonly Strip[] | undefined;
}) {
  const previews = differing(page.edits, page.drawnFrom).filter(({ span }) => span.id !== page.typingIn);
  return previews.map(({ span, text, redacted, strategy }) => {
    // A redaction leaves the page bare where its words were, as the download will.
    if (redacted) {
      return (
        <span
          key={span.id}
          className={styles.preview}
          aria-hidden="true"
          data-preview=""
          style={boxOf(span.bbox, info)}
        />
      );
    }
    // Put back: the page's own pixels are exactly right, under any strip still drawn over its row.
    if (text === span.text) {
      return isUnderStrip(span.bbox, { strips, info }) ? (
        <Original key={span.id} box={stripBoxOf(span, info)} info={info} src={page.src} />
      ) : null;
    }
    return (
      <Preview
        key={span.id}
        span={span}
        info={info}
        drawn={{ text, strategy, font: fontOf(page.fonts, span), rules: page.rules }}
      />
    );
  });
}

/**
 * The original page's pixels over `box`, where a strip of its row still shows an edit gone since: a span put back,
 * or new text moved. As far as the server would redraw, so the old edit's ink goes too.
 */
function Original({
  box,
  info,
  src,
}: {
  box: { x0: number; y0: number; x1: number; y1: number };
  info: PageInfo;
  src: string;
}) {
  return (
    <span className={styles.original} aria-hidden="true" data-original="" style={boxOf(box, info)}>
      <img src={src} alt="" decoding="async" style={cropOf(box, info)} />
    </span>
  );
}

/** The browser's drawing of a span's text, while the server's strip under it shows other words: in the way out asked for. */
function Preview({
  span,
  info,
  drawn: { text, strategy, font, rules },
}: {
  span: SpanInfo;
  info: PageInfo;
  drawn: { text: string; strategy: Strategy | undefined; font: FontInfo | undefined; rules: FitRules };
}) {
  if (font === undefined) return null;
  const face = previewFaceOf(span, { font, text });
  const setting = settingOf(span, { font, text, asked: strategy, rules });
  return (
    <span
      className={styles.preview}
      aria-hidden="true"
      data-preview=""
      style={previewStyleOf(span, info, { face, scale: setting })}
    >
      {text}
    </span>
  );
}
