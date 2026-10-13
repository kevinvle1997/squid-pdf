import { DropZone, FileTrigger, isFileDropItem } from "react-aria-components";
import { Button } from "../../ui/Button";
import { Num } from "../../ui/Num";
import { Warn } from "../../ui/Warn";
import { Wordmark } from "../../ui/Wordmark";
import { QUIET_MS } from "../constants";
import type { Landing } from "../open";
import styles from "./Drop.module.css";

const PERCENT = new Intl.NumberFormat("en", { style: "percent" });

/** The landing: the whole window takes a dropped PDF. No tool grid, no sign-in. */
export function Drop({ landing, onChoose }: { landing: Landing; onChoose: (file: File) => void }) {
  return (
    <DropZone
      className={styles.zone}
      aria-label="Drop a PDF to open it"
      onDrop={async (event) => {
        const item = event.items.find(isFileDropItem);
        if (item !== undefined) onChoose(await item.getFile());
      }}
    >
      <header className={styles.top}>
        <Wordmark />
      </header>
      <main className={styles.middle}>
        <h1 className={styles.title}>Drop a PDF anywhere to fix its words</h1>
        <FileTrigger acceptedFileTypes={["application/pdf"]} onSelect={(files) => files?.[0] && onChoose(files[0])}>
          <Button className={styles.choose} isDisabled={landing.kind === "opening"}>
            Choose a PDF
          </Button>
        </FileTrigger>
        <p className={styles.status} role="status">
          {landing.kind === "opening" && <Progress {...landing} />}
          {landing.kind === "failed" && <Warn>{landing.detail}</Warn>}
        </p>
        <p className={styles.fine}>Your file is deleted an hour after you last touch it.</p>
      </main>
    </DropZone>
  );
}

function Progress({ name, sent, total }: { name: string; sent: number; total: number }) {
  // Sent, the server is reading the text: a real step, not invented progress.
  const said =
    total > 0 && sent >= total ? (
      <>Reading the text of {name}</>
    ) : (
      <>
        Sending {name} <Num>{PERCENT.format(total > 0 ? sent / total : 0)}</Num>
      </>
    );
  // Hidden for the quiet spell by CSS, so a quick open never flashes it and no timer is needed.
  return (
    <span className={styles.progress} style={{ animationDelay: `${QUIET_MS}ms` }}>
      {said}
    </span>
  );
}
