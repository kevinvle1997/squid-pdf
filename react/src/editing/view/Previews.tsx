// What the browser draws over a page until the server's render shows it: a span's new words, a
// redaction's bare paper, the page's own pixels where a span is put back, and the settle as a render lands.
import type { FitRules, FontInfo, PageInfo, SpanInfo, Strategy } from "../../api/types";
import { previewFaceOf } from "../faces";
import { settingOf } from "../fit";
import type { PageState } from "../page";
import { differing } from "../project";
import { isUnderStrip, stripBoxOf } from "../redrawRegions";
import type { Strip } from "../render";
import { boxOf, cropOf, previewStyleOf } from "./geometry";
import styles from "./Previews.module.css";

/** Where the render said other than the preview, its words come up from faint as it lands: drawn anew each landing. */
export function Settles({ page, info }: { page: PageState; info: PageInfo }) {
  const { disagreement } = page;
  if (disagreement === undefined) return null;
  return page.spans
    .filter((span) => disagreement.spanIds.has(span.id))
    .map((span) => (
      <span
        key={`${span.id}:${disagreement.count}`}
        className={styles.settle}
        aria-hidden="true"
        data-settle=""
        style={boxOf(span.bbox, info)}
      />
    ));
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
      return isUnderStrip(span, { strips, info }) ? (
        <Original key={span.id} span={span} info={info} src={page.src} />
      ) : null;
    }
    return (
      <Preview
        key={span.id}
        span={span}
        info={info}
        drawn={{ text, strategy, font: page.fonts.get(span.font), rules: page.rules }}
      />
    );
  });
}

/** The original page's pixels over a span put back, where a strip of its row still shows another edit. */
function Original({ span, info, src }: { span: SpanInfo; info: PageInfo; src: string }) {
  // As far as the server would redraw, so the old edit's ink goes too.
  const box = stripBoxOf(span, info);
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
