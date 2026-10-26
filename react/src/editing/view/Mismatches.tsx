// The bar's warned count, pressed: where an edit won't match, grouped by why (quality.md, Rule 1).
import { type ReactNode, useMemo } from "react";
import { Dialog, DialogTrigger, Heading, Popover } from "react-aria-components";
import { Button } from "../../ui/Button";
import { EyeToggle } from "../../ui/EyeToggle";
import { BELOW_BAR_PX } from "../constants";
import { changedMismatches } from "../editor";
import { readableName } from "../fonts";
import { type Mismatches, type MismatchRow, mismatchesOf } from "../mismatches";
import { toggleShownMismatches } from "../shownMismatches";
import { shallowEqual } from "../store";
import { counted } from "../words";
import { useEditor, useEditorState } from "./context";
import styles from "./Mismatches.module.css";

const PLACES = { one: "place", other: "places" };

/** The count, as a button that opens its places grouped: the whole document's before any change, else the changes'. */
export function MismatchCount({ changed, children }: { changed: boolean; children: ReactNode }) {
  const showing = useEditorState((state) => state.shownMismatches.size > 0);
  return (
    <DialogTrigger>
      {/* One run of words, so the button's gap falls only before its chevron, which says it opens a list. */}
      <Button variant="quiet" className={styles.count}>
        <span>
          {children}
          {showing && <span className="vh"> (shown on the page)</span>}
        </span>
        {showing && <ShownMark />}
        <Chevron />
      </Button>
      <Popover placement="bottom end" offset={BELOW_BAR_PX} className={styles.popover}>
        <Dialog aria-label="Where edits won't match" className={styles.dialog}>
          {/* Showing a row's places shuts the list, so it doesn't lie over the marks it shows. */}
          {({ close }) => (changed ? <ChangedGroups shown={close} /> : <AtRestGroups shown={close} />)}
        </Dialog>
      </Popover>
    </DialogTrigger>
  );
}

/** A chevron, drawn: the count opens a list, as a menu's button does. */
function Chevron() {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 10 10"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={styles.chevron}
    >
      <path d="M2.5 3.75 5 6.25l2.5-2.5" />
    </svg>
  );
}

/** An open eye, drawn small: some of the count's places are marked on the page now. */
function ShownMark() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      className={styles.chevron}
    >
      <path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z" strokeLinejoin="round" />
      <circle cx="8" cy="8" r="2" />
    </svg>
  );
}

/** Every place in the document, worked out once as it opened. */
function AtRestGroups({ shown }: { shown: () => void }) {
  const mismatches = useEditorState((state) => state.layout.mismatchesAtRest);
  return <Groups mismatches={mismatches} shown={shown} />;
}

/** The changed places, grouped while the list is open. */
function ChangedGroups({ shown }: { shown: () => void }) {
  const { reading, drawn, fonts, sentences } = useEditorState(
    (state) => ({
      reading: state.reading,
      drawn: state.drawn,
      fonts: state.layout.fonts,
      sentences: state.doc.sentences,
    }),
    shallowEqual,
  );
  const mismatches = useMemo(
    () => mismatchesOf(changedMismatches({ reading, drawn }), { fonts, sentences }),
    [reading, drawn, fonts, sentences],
  );
  return <Groups mismatches={mismatches} shown={shown} />;
}

/** The fonts a similar one draws for, then the reasons the file's own won't match, each row with its places. */
function Groups({ mismatches, shown }: { mismatches: Mismatches; shown: () => void }) {
  return (
    <>
      {mismatches.fonts.length > 0 && (
        <section className={styles.group}>
          <Heading level={2} className={styles.heading}>
            In a similar font
          </Heading>
          <ul className={styles.list}>
            {mismatches.fonts.map((row) => (
              <li key={row.key} className={styles.row}>
                <span className={styles.name}>{readableName(row.name)}</span>
                <span className={styles.places}>{counted(row.places, PLACES)}</span>
                <ShowOnPage row={row} what={`the places in ${readableName(row.name)}`} shown={shown} />
                <span className={styles.sentence}>{row.sentence}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {mismatches.reasons.length > 0 && (
        <section className={styles.group}>
          <Heading level={2} className={styles.heading}>
            Will look different
          </Heading>
          <ul className={styles.list}>
            {mismatches.reasons.map((row) => (
              <li key={row.key} className={styles.row}>
                <span className={styles.sentence}>{row.sentence}</span>
                <span className={styles.places}>{counted(row.places, PLACES)}</span>
                <ShowOnPage row={row} what={counted(row.places, PLACES)} shown={shown} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

/** The row's eye: marks its places on the page where they are, until it's pressed again; showing them, it shuts the list. */
function ShowOnPage({ row, what, shown }: { row: MismatchRow; what: string; shown: () => void }) {
  const editor = useEditor();
  const isShown = useEditorState((state) => state.shownMismatches.has(row.key));
  return (
    <EyeToggle
      label={`Show ${what} on the page`}
      isSelected={isShown}
      onChange={(selected) => {
        toggleShownMismatches(editor, row);
        if (selected) shown();
      }}
    />
  );
}
