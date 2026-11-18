import { useRef } from "react";
import { DropZone, FileTrigger, isFileDropItem } from "react-aria-components";
import type { DocumentsLeft } from "../../api/types";
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
const DAY = new Intl.DateTimeFormat("en", { day: "numeric", month: "long" });
const WHOLE = new Intl.NumberFormat("en");
const PLURAL = new Intl.PluralRules("en");

interface Props {
  landing: Landing;
  termsHref: string | null; // where the service's terms are read, once the server says it has them
  persisted: Persisted<unknown> | null; // offered while the landing waits
  forgetFailed: string | null; // why "Delete it now" left it there, in words for the user
  signedInAs: string | null; // the address this browser is signed in as, on a server that keeps accounts
  documentsLeft: DocumentsLeft | null; // this month's the account has left, where a limit applies to it
  paidUntil: number | null; // the account has no document limit until then, Unix seconds, its month paid
  payment: { sentence: string; failed: boolean } | null; // what paying, or coming back from it, came to
  goingToStripe: boolean; // the server is asked for Stripe's page, so its buttons wait
  onPay: (() => void) | null; // offered where the server takes payment and the account has a limit to lift
  onManageBilling: (() => void) | null; // offered where the account has billing in Stripe's portal
  onSignOut: () => void;
  onChoose: (file: File) => void;
  onReopen: () => void; // the document persisted here opens again, with its changes
  onForget: () => void; // the document persisted here is deleted from this browser
}

/** The landing: the whole window takes a dropped PDF, a sheet says so, and what becomes of it is told under the sheet. No sign-in unless the server asks for one. */
export function Drop({
  landing,
  termsHref,
  persisted,
  forgetFailed,
  signedInAs,
  documentsLeft,
  paidUntil,
  payment,
  goingToStripe,
  onPay,
  onManageBilling,
  onSignOut,
  onChoose,
  onReopen,
  onForget,
}: Props) {
  const offered = landing.kind === "waiting" ? persisted : null;
  const choose = useRef<HTMLButtonElement>(null);
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
        foot={<Facts termsHref={termsHref} />}
      >
        <p className={styles.then}>Then click any word to fix it.</p>
        <FileTrigger acceptedFileTypes={["application/pdf"]} onSelect={(files) => files?.[0] && onChoose(files[0])}>
          <Button ref={choose} variant="primary" className={styles.choose} isDisabled={landing.kind === "opening"}>
            Choose a PDF
          </Button>
        </FileTrigger>
        <p className={styles.status} role="status">
          {landing.kind === "opening" && <Progress {...landing} />}
          {landing.kind === "failed" && <Warn mark>{landing.detail}</Warn>}
          {landing.kind === "month used" && <Warn mark>{landing.detail}</Warn>}
          {offered !== null && forgetFailed !== null && <Warn mark>{forgetFailed}</Warn>}
          {payment !== null && (payment.failed ? <Warn mark>{payment.sentence}</Warn> : payment.sentence)}
        </p>
        {landing.kind === "month used" && onPay !== null && (
          <Button className={styles.pay} onPress={onPay} isDisabled={goingToStripe}>
            Pay for no limit
          </Button>
        )}
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
        {signedInAs !== null && (
          <p className={styles.signedIn}>
            <span>Signed in as {signedInAs}</span>
            {/* Past the month's, the status says so already. */}
            {documentsLeft !== null && landing.kind !== "month used" && <LeftThisMonth {...documentsLeft} />}
            {paidUntil !== null && <span>No document limit until {DAY.format(paidUntil * 1000)}</span>}
            {onManageBilling !== null && (
              <Button variant="quiet" onPress={onManageBilling} isDisabled={goingToStripe}>
                Manage billing
              </Button>
            )}
            {/* Quiet: the sheet's one action is choosing a PDF. */}
            {/* Its line goes once signed out, so focus goes on to the sheet's one action, never to body. */}
            <Button
              variant="quiet"
              onPress={() => {
                choose.current?.focus();
                onSignOut();
              }}
            >
              Sign out
            </Button>
          </p>
        )}
      </SheetScreen>
    </DropZone>
  );
}

/** How many of this month's documents are left: "2 of 3 documents left this month". */
function LeftThisMonth({ left, monthly }: DocumentsLeft) {
  const documents = PLURAL.select(monthly) === "one" ? "document" : "documents";
  return (
    <span>
      <Num>{WHOLE.format(left)}</Num> of <Num>{WHOLE.format(monthly)}</Num> {documents} left this month
    </span>
  );
}

/** What becomes of the file, told first and largest under the sheet with the code that does it and any terms, then what the tool does, in two facts. */
function Facts({ termsHref }: { termsHref: string | null }) {
  return (
    <div className={styles.facts}>
      <div className={styles.promise}>
        <h2 className={styles.promiseTitle}>
          {/* An hour is the server's IDLE_S, held to these words by tests/documents/test_store.py. */}
          We <mark>delete your file</mark> an hour after you're done.
        </h2>
        <p className={styles.promiseSentence}>
          After that, we keep no copy of it or your edits. squid-pdf is open source, so you can{" "}
          <a className={styles.source} href={__SOURCE_CODE__}>
            read the code that does it
          </a>
          .
          {termsHref !== null && (
            <>
              {" "}
              <a className={styles.terms} href={termsHref}>
                Terms of use
              </a>
            </>
          )}
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
