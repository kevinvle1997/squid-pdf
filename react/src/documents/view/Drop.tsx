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

/** The landing: the whole window takes a dropped PDF, a sheet says so, and what becomes of it is said under the sheet. No sign-in. */
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
            Drop a PDF <mark className={styles.typed}>here</mark>
          </>
        }
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

/** What becomes of the file, said first and largest under the sheet, then what the tool does, in two facts. */
function Facts() {
  return (
    <div className={styles.facts}>
      <div className={styles.promise}>
        <h2 className={styles.promiseTitle}>
          We <mark className={styles.typed}>delete your file</mark> an hour after you're done.
        </h2>
        <p className={styles.promiseSaid}>After that, we keep no copy of it or your edits.</p>
      </div>
      <ul className={styles.factList}>
        <Fact title="Fix any word">Click it and type.</Fact>
        <Fact title="Know before you save">We'll tell you if a change won't look the same.</Fact>
      </ul>
    </div>
  );
}

/** One fact: a few words, and a line under them. */
function Fact({ title, children }: { title: string; children: string }) {
  return (
    <li className={styles.fact}>
      <span className={styles.factTitle}>{title}</span>
      <span className={styles.factSaid}>{children}</span>
    </li>
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
