import { DropZone, FileTrigger, isFileDropItem } from "react-aria-components";
import { AfterDelay } from "../../ui/AfterDelay";
import { Button } from "../../ui/Button";
import { Num } from "../../ui/Num";
import { SheetScreen } from "../../ui/SheetScreen";
import { Warn } from "../../ui/Warn";
import type { Persisted } from "../localPersistence";
import { type Landing, shareSent } from "../open";
import styles from "./Drop.module.css";

const PERCENT = new Intl.NumberFormat("en", { style: "percent" });
const TIME = new Intl.DateTimeFormat("en", { hour: "numeric", minute: "2-digit" });

interface Props {
  landing: Landing;
  persisted: Persisted<unknown> | null; // offered while the landing waits
  forgetFailed: string | null; // why "Delete it now" left it there, in words for the user
  onChoose: (file: File) => void;
  onReopen: () => void; // the document persisted here opens again, with its changes
  onForget: () => void; // the document persisted here is deleted from this browser
}

/** The landing: the whole window takes a dropped PDF, a sheet says so, and what becomes of it is told under the sheet. No sign-in. */
export function Drop({ landing, persisted, forgetFailed, onChoose, onReopen, onForget }: Props) {
  const offered = landing.kind === "waiting" ? persisted : null;
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
            <span className={styles.byPointer}>Drop</span>
            <span className={styles.byTouch}>Open</span> a PDF <mark className={styles.typed}>here</mark>
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
          {offered !== null && forgetFailed !== null && <Warn mark>{forgetFailed}</Warn>}
        </p>
        {offered !== null && (
          <div className={styles.persisted}>
            <p className={styles.persistedSentence}>
              Your changes to {offered.file.name} are kept in this browser until {TIME.format(offered.until)}.
            </p>
            <div className={styles.persistedActions}>
              <Button onPress={onReopen}>Open it again</Button>
              {/* Quiet beside it: opening is the likely press, and deleting can't be undone. */}
              <Button variant="quiet" onPress={onForget}>
                Delete it now
              </Button>
            </div>
          </div>
        )}
      </SheetScreen>
    </DropZone>
  );
}

/** What becomes of the file, told first and largest under the sheet with the code that does it, then what the tool does, in two facts. */
function Facts() {
  return (
    <div className={styles.facts}>
      <div className={styles.promise}>
        <h2 className={styles.promiseTitle}>
          We <mark>delete your file</mark> an hour after you're done.
        </h2>
        <p className={styles.promiseSentence}>
          After that, we keep no copy of it or your edits. squid-pdf is open source, so you can{" "}
          <a className={styles.source} href={__SOURCE_CODE__}>
            read the code that does it
          </a>
          .
        </p>
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
      <span className={styles.factSentence}>{children}</span>
    </li>
  );
}

/** How far a file on its way has got: sending it, then the server reading it. A quick open shows nothing. */
function Progress({ name, sent, total }: { name: string; sent: number; total: number }) {
  const share = shareSent({ sent, total });
  if (share === null) return <AfterDelay>Reading the text of {name}</AfterDelay>;
  return (
    <AfterDelay>
      Sending {name} <Num>{PERCENT.format(share)}</Num>
    </AfterDelay>
  );
}
