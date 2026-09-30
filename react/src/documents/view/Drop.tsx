import { useState } from "react";
import { DropZone, FileTrigger, isFileDropItem } from "react-aria-components";
import { Button } from "../../ui/Button";
import { Num } from "../../ui/Num";
import { Warn } from "../../ui/Warn";
import { Wordmark } from "../../ui/Wordmark";
import { QUIET_MS } from "../constants";
import { type Opened, openFile } from "../open";
import styles from "./Drop.module.css";

type State =
  | { kind: "waiting" }
  | { kind: "opening"; name: string; sent: number; total: number }
  | { kind: "failed"; detail: string };

const PERCENT = new Intl.NumberFormat("en", { style: "percent" });

/** The landing: the whole window takes a dropped PDF. No tool grid, no sign-in. */
export function Drop({ onOpened, onOpening }: { onOpened: (opened: Opened) => void; onOpening: () => void }) {
  const [state, setState] = useState<State>({ kind: "waiting" });

  async function open(file: File) {
    // One file at a time: a second drop mid-upload would race the first to the editor.
    if (state.kind === "opening") return;
    onOpening();
    setState({ kind: "opening", name: file.name, sent: 0, total: file.size });
    const opening = await openFile(file, (sent, total) =>
      setState((now) => (now.kind === "opening" ? { ...now, sent, total } : now)),
    );
    if ("opened" in opening) onOpened(opening.opened);
    else setState({ kind: "failed", detail: opening.failed });
  }

  return (
    <DropZone
      className={styles.zone}
      aria-label="Drop a PDF to open it"
      onDrop={async (event) => {
        const item = event.items.find(isFileDropItem);
        if (item !== undefined) await open(await item.getFile());
      }}
    >
      <header className={styles.top}>
        <Wordmark />
      </header>
      <main className={styles.middle}>
        <h1 className={styles.title}>Drop a PDF anywhere to fix its words</h1>
        <FileTrigger acceptedFileTypes={["application/pdf"]} onSelect={(files) => files?.[0] && open(files[0])}>
          <Button className={styles.choose} isDisabled={state.kind === "opening"}>
            Choose a PDF
          </Button>
        </FileTrigger>
        <p className={styles.status} role="status">
          {state.kind === "opening" && <Progress {...state} />}
          {state.kind === "failed" && <Warn>{state.detail}</Warn>}
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
