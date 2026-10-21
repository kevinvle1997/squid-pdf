import { DropZone, FileTrigger, isFileDropItem } from "react-aria-components";
import { AfterQuiet } from "../../ui/AfterQuiet";
import { Button } from "../../ui/Button";
import { Num } from "../../ui/Num";
import { SheetScreen } from "../../ui/SheetScreen";
import { Warn } from "../../ui/Warn";
import type { Kept } from "../kept";
import { type Landing, shareSent } from "../open";
import styles from "./Drop.module.css";

const PERCENT = new Intl.NumberFormat("en", { style: "percent" });
const TIME = new Intl.DateTimeFormat("en", { hour: "numeric", minute: "2-digit" });

interface Props {
  landing: Landing;
  kept: Kept<unknown> | null; // what this browser kept, offered while the landing waits
  onChoose: (file: File) => void;
  onReopen: () => void; // the document kept here opens again, with its changes
  onForget: () => void; // the document kept here is deleted from this browser
}

/** The landing: the whole window takes a dropped PDF, a sheet says so, and three facts say what then. No sign-in. */
export function Drop({ landing, kept, onChoose, onReopen, onForget }: Props) {
  const offered = landing.kind === "waiting" ? kept : null;
  return (
    <DropZone
      className={styles.zone}
      aria-label="Drop a PDF to open it"
      onDrop={async (event) => {
        const item = event.items.find(isFileDropItem);
        if (item !== undefined) onChoose(await item.getFile());
      }}
    >
      <SheetScreen
        title={
          <>
            Drop a PDF <mark>here</mark>
          </>
        }
        says="Got a typo? I can help."
        sheetClassName={styles.sheet}
        foot={<Facts />}
      >
        <p className={styles.then}>Then click any word to fix it.</p>
        <FileTrigger acceptedFileTypes={["application/pdf"]} onSelect={(files) => files?.[0] && onChoose(files[0])}>
          <Button variant="primary" className={styles.choose} isDisabled={landing.kind === "opening"}>
            Choose a PDF
          </Button>
        </FileTrigger>
        <p className={styles.status} role="status">
          {landing.kind === "opening" && <Progress {...landing} />}
          {landing.kind === "failed" && <Warn mark>{landing.detail}</Warn>}
        </p>
        {offered !== null && (
          <div className={styles.kept}>
            <p className={styles.keptSaid}>
              Your changes to {offered.file.name} are kept in this browser until {TIME.format(offered.until)}.
            </p>
            <div className={styles.keptActions}>
              <Button onPress={onReopen}>Open it again</Button>
              <Button onPress={onForget}>Delete it now</Button>
            </div>
          </div>
        )}
      </SheetScreen>
    </DropZone>
  );
}

/** What the tool does, and what becomes of the file, in three short facts under the sheet. */
function Facts() {
  return (
    <ul className={styles.facts}>
      <li className={styles.fact}>
        <span className={styles.factIcon}>
          <svg viewBox="0 0 22 22" aria-hidden="true">
            <path d="M4 6h10M4 11h7M4 16h10M17 4v14M15 4h4M15 18h4" />
          </svg>
        </span>
        <span className={styles.factTitle}>Fix words on the page</span>
        <span className={styles.factSaid}>Click a word and type.</span>
      </li>
      <li className={styles.fact}>
        <span className={styles.factIcon}>
          <svg viewBox="0 0 22 22" aria-hidden="true">
            <path d="M1.5 11S5 4.5 11 4.5 20.5 11 20.5 11 17 17.5 11 17.5 1.5 11 1.5 11z" />
            <circle cx="11" cy="11" r="3" />
          </svg>
        </span>
        <span className={styles.factTitle}>Know before you save</span>
        <span className={styles.factSaid}>We'll tell you if a change won't look the same.</span>
      </li>
      <li className={styles.fact}>
        <span className={styles.factIcon}>
          <svg viewBox="0 0 22 22" aria-hidden="true">
            <circle cx="11" cy="11" r="8.5" />
            <path d="M11 6v5l3.5 2" />
          </svg>
        </span>
        <span className={styles.factTitle}>Gone after an hour</span>
        <span className={styles.factSaid}>
          Your file is deleted from our server and this browser an hour after you're done.
        </span>
      </li>
    </ul>
  );
}

/** How far a file on its way has got: sending it, then the server reading it. A quick open shows nothing. */
function Progress({ name, sent, total }: { name: string; sent: number; total: number }) {
  const share = shareSent({ sent, total });
  if (share === null) return <AfterQuiet>Reading the text of {name}</AfterQuiet>;
  return (
    <AfterQuiet>
      Sending {name} <Num>{PERCENT.format(share)}</Num>
    </AfterQuiet>
  );
}
