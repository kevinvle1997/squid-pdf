import type { Terms, TermsPiece } from "../../api/types";
import { AfterDelay } from "../../ui/AfterDelay";
import { Button } from "../../ui/Button";
import { ScreenTop } from "../../ui/ScreenTop";
import type { TermsAnswer } from "../terms";
import styles from "./TermsScreen.module.css";

// Its heading until the deployment's own title comes, and where it can't.
const HEADING = "Terms of use";
// How many paragraphs' shape shows while the terms are asked for.
const SHAPED_PARAGRAPHS = 3;

/** The service's terms on a page of the app's own, with the way home at its top and its foot. */
export function TermsScreen({ answer }: { answer: Exclude<TermsAnswer, { kind: "none" }> }) {
  return (
    <div className={styles.screen}>
      <title>{`${answer.kind === "read" ? answer.terms.title : HEADING} · squid-pdf`}</title>
      <ScreenTop home />
      <main className={styles.terms}>
        {answer.kind === "read" ? <Read terms={answer.terms} /> : <h1 className={styles.title}>{HEADING}</h1>}
        {answer.kind !== "read" && (
          // Kept while the terms are asked for, so the failure's words are heard as they come.
          <p className={styles.status} role="status">
            {answer.kind === "asking" ? <AfterDelay>Reading the terms</AfterDelay> : answer.detail}
          </p>
        )}
        {answer.kind === "asking" && <Shape />}
        {answer.kind === "failed" && (
          <Button variant="primary" className={styles.reload} onPress={() => window.location.reload()}>
            Reload
          </Button>
        )}
        <p className={styles.foot}>
          <a className={styles.home} href="/">
            Back to squid-pdf
          </a>
        </p>
      </main>
    </div>
  );
}

/** The terms as the deployment wrote them: the title, when they last changed, then each paragraph led in bold. */
function Read({ terms }: { terms: Terms }) {
  return (
    <>
      <div className={styles.titled}>
        <h1 className={styles.title}>{terms.title}</h1>
        {terms.last_changed !== null && <p className={styles.lastChanged}>{terms.last_changed}</p>}
      </div>
      {/* Keyed by place: the file's order never changes while it's shown. */}
      {terms.paragraphs.map(({ lead, pieces }, index) => (
        <p key={index} className={styles.paragraph}>
          <strong className={styles.lead}>{lead}</strong>{" "}
          {pieces.map((piece, at) => (
            <Piece key={at} piece={piece} />
          ))}
        </p>
      ))}
    </>
  );
}

/** One piece of a paragraph: its words, or an address to write to. */
function Piece({ piece }: { piece: TermsPiece }) {
  if (piece.kind === "words") return piece.text;
  return (
    <a className={styles.email} href={`mailto:${piece.address}`}>
      {piece.address}
    </a>
  );
}

/** The shape of the paragraphs on their way: lines, drawn quietly, that no screen reader reads. */
function Shape() {
  return (
    <div className={styles.shape} aria-hidden="true">
      {Array.from({ length: SHAPED_PARAGRAPHS }, (_, index) => (
        <div key={index} className={styles.shapedParagraph} />
      ))}
    </div>
  );
}
